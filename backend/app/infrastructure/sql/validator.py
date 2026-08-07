from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect


@dataclass(frozen=True, slots=True)
class SQLPolicy:
    allowed_tables: frozenset[str]
    prohibited_columns: frozenset[str] = frozenset()
    prohibited_functions: frozenset[str] = frozenset(
        {"pg_sleep", "sleep", "benchmark", "load_file"}
    )
    maximum_joins: int = 6
    maximum_rows: int = 1000
    allow_select_star: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedSQL:
    normalized_sql: str
    referenced_tables: frozenset[str]
    referenced_columns: frozenset[str]
    join_count: int


class SQLValidator:
    def validate(
        self,
        *,
        sql: str,
        dialect: SQLDialect,
        policy: SQLPolicy,
    ) -> ValidatedSQL:
        try:
            statements = parse(sql, read=dialect.value)
        except ParseError as exc:
            raise Prompt2InsightError(
                ErrorCode.SQL_PARSE_FAILED,
                "The generated SQL could not be parsed.",
            ) from exc

        if len(statements) != 1:
            raise self._reject("Exactly one SQL statement is required.")

        tree = statements[0]
        if not isinstance(tree, exp.Query):
            raise self._reject("Only SELECT queries and SELECT-based CTEs are allowed.")

        if not policy.allow_select_star and any(tree.find_all(exp.Star)):
            raise self._reject("SELECT * is not allowed.")

        joins = list(tree.find_all(exp.Join))
        if len(joins) > policy.maximum_joins:
            raise self._reject("The query exceeds the maximum join count.")

        tables = frozenset(self._table_name(table) for table in tree.find_all(exp.Table))
        unknown_tables = tables - policy.allowed_tables
        if unknown_tables:
            raise self._reject(
                f"Unapproved tables: {', '.join(sorted(unknown_tables))}."
            )

        columns = frozenset(self._column_name(column) for column in tree.find_all(exp.Column))
        prohibited = {
            column
            for column in columns
            if column in policy.prohibited_columns
            or column.split(".")[-1] in policy.prohibited_columns
        }
        if prohibited:
            raise self._reject(
                f"Prohibited columns: {', '.join(sorted(prohibited))}."
            )

        functions = {
            function.sql_name().lower()
            for function in tree.find_all(exp.Func)
            if function.sql_name()
        }
        blocked = functions & policy.prohibited_functions
        if blocked:
            raise self._reject(
                f"Prohibited functions: {', '.join(sorted(blocked))}."
            )

        self._enforce_limit(tree, policy.maximum_rows)

        return ValidatedSQL(
            normalized_sql=tree.sql(dialect=dialect.value),
            referenced_tables=tables,
            referenced_columns=columns,
            join_count=len(joins),
        )

    @staticmethod
    def _table_name(table: exp.Table) -> str:
        return ".".join(part for part in (table.catalog, table.db, table.name) if part)

    @staticmethod
    def _column_name(column: exp.Column) -> str:
        return ".".join(
            part
            for part in (column.catalog, column.db, column.table, column.name)
            if part
        )

    @staticmethod
    def _enforce_limit(tree: exp.Query, maximum_rows: int) -> None:
        limit = tree.args.get("limit")
        if limit is None:
            tree.limit(maximum_rows, copy=False)
            return

        expression = limit.expression
        if isinstance(expression, exp.Literal) and expression.is_int:
            if int(expression.this) > maximum_rows:
                limit.set("expression", exp.Literal.number(maximum_rows))
            return

        raise Prompt2InsightError(
            ErrorCode.SQL_POLICY_REJECTED,
            "LIMIT must be a numeric literal.",
        )

    @staticmethod
    def _reject(message: str) -> Prompt2InsightError:
        return Prompt2InsightError(ErrorCode.SQL_POLICY_REJECTED, message)
