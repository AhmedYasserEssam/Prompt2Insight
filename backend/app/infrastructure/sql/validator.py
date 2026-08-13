from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog, ColumnClassification


@dataclass(frozen=True, slots=True)
class SQLPolicy:
    allowed_tables: frozenset[str]
    allowed_columns: frozenset[str] = frozenset()
    prohibited_columns: frozenset[str] = frozenset()
    sensitive_columns: frozenset[str] = frozenset()
    prohibited_functions: frozenset[str] = frozenset(
        {"pg_sleep", "sleep", "benchmark", "load_file"}
    )
    maximum_joins: int = 6
    maximum_rows: int = 1000
    allow_select_star: bool = False
    allowed_joins: frozenset[tuple[str, str, str]] = frozenset()
    allowed_functions: frozenset[str] = frozenset(
        {
            "abs",
            "avg",
            "cast",
            "coalesce",
            "concat",
            "count",
            "date_format",
            "date_trunc",
            "extract",
            "lower",
            "max",
            "min",
            "nullif",
            "round",
            "sum",
            "timestamp_trunc",
            "upper",
        }
    )

    @classmethod
    def from_catalog(
        cls,
        *,
        catalog: AnalyticsCatalog,
        allowed_tables: frozenset[str],
        prohibited_functions: frozenset[str] = frozenset(
            {"pg_sleep", "sleep", "benchmark", "load_file"}
        ),
        maximum_joins: int = 6,
        maximum_rows: int = 1000,
        allow_select_star: bool = False,
    ) -> "SQLPolicy":
        prohibited_columns = frozenset(
            column
            for column, classification in catalog.column_policies.items()
            if classification is ColumnClassification.PROHIBITED
        )
        sensitive_columns = frozenset(
            column
            for column, classification in catalog.column_policies.items()
            if classification is ColumnClassification.SENSITIVE
        )
        return cls(
            allowed_tables=allowed_tables,
            prohibited_columns=prohibited_columns,
            sensitive_columns=sensitive_columns,
            prohibited_functions=prohibited_functions,
            maximum_joins=maximum_joins,
            maximum_rows=maximum_rows,
            allow_select_star=allow_select_star,
            allowed_joins=catalog.sql_join_contracts(),
        )


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
        if not isinstance(tree, exp.Query) or any(
            isinstance(
                node,
                (
                    exp.Insert,
                    exp.Update,
                    exp.Delete,
                    exp.Merge,
                    exp.Create,
                    exp.Drop,
                    exp.Alter,
                    exp.TruncateTable,
                    exp.Command,
                ),
            )
            for node in tree.walk()
        ):
            raise self._reject("Only SELECT queries and SELECT-based CTEs are allowed.")

        if tree.find(exp.Into) is not None or tree.find(exp.Lock) is not None:
            raise self._reject("SELECT forms with writes or locks are not allowed.")

        if not policy.allow_select_star and any(tree.find_all(exp.Star)):
            raise self._reject("SELECT * is not allowed.")

        joins = list(tree.find_all(exp.Join))
        if len(joins) > policy.maximum_joins:
            raise self._reject("The query exceeds the maximum join count.")

        cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        tables = frozenset(
            self._table_name(table)
            for table in tree.find_all(exp.Table)
            if self._table_name(table) not in cte_names
        )
        unknown_tables = tables - policy.allowed_tables
        if unknown_tables:
            raise Prompt2InsightError(
                ErrorCode.UNAUTHORIZED_TABLE,
                f"Unapproved tables: {', '.join(sorted(unknown_tables))}.",
            )

        self._validate_joins(tree, joins, policy.allowed_joins)

        columns = self._canonical_columns(tree, policy.allowed_columns, cte_names)
        prohibited = {column for column in columns if column in policy.prohibited_columns}
        if prohibited:
            raise Prompt2InsightError(
                ErrorCode.UNAUTHORIZED_COLUMN,
                f"Prohibited columns: {', '.join(sorted(prohibited))}.",
            )
        sensitive = {column for column in columns if column in policy.sensitive_columns}
        if sensitive:
            raise Prompt2InsightError(
                ErrorCode.UNAUTHORIZED_COLUMN,
                f"Sensitive columns are not queryable: {', '.join(sorted(sensitive))}.",
            )

        functions = {
            name
            for function in tree.find_all(exp.Func)
            if (name := self._function_name(function)) is not None
        }
        blocked = functions & policy.prohibited_functions
        unknown_functions = functions - policy.allowed_functions
        if blocked or unknown_functions:
            raise self._reject(
                f"Unapproved functions: {', '.join(sorted(blocked | unknown_functions))}."
            )

        self._enforce_limit(tree, policy.maximum_rows)

        return ValidatedSQL(
            normalized_sql=self._normalized_sql(tree, dialect),
            referenced_tables=tables,
            referenced_columns=columns,
            join_count=len(joins),
        )

    @staticmethod
    def _normalized_sql(tree: exp.Query, dialect: SQLDialect) -> str:
        normalized_tree = tree.copy()
        for placeholder in normalized_tree.find_all(exp.Placeholder):
            if placeholder.this:
                placeholder.replace(exp.Var(this=f":{placeholder.name}"))
        return normalized_tree.sql(dialect=dialect.value)

    @staticmethod
    def _table_name(table: exp.Table) -> str:
        return ".".join(part for part in (table.catalog, table.db, table.name) if part)

    @staticmethod
    def _column_name(column: exp.Column) -> str:
        return ".".join(
            part for part in (column.catalog, column.db, column.table, column.name) if part
        )

    def _canonical_columns(
        self,
        tree: exp.Query,
        allowed_columns: frozenset[str],
        cte_names: set[str],
    ) -> frozenset[str]:
        aliases = {
            table.alias_or_name: self._table_name(table) for table in tree.find_all(exp.Table)
        }
        columns: set[str] = set()
        for column in tree.find_all(exp.Column):
            if self._is_order_projection_alias(column):
                continue
            if column.table:
                if column.table in cte_names:
                    # The CTE body is visited independently; its derived output is not a
                    # physical schema column and therefore cannot widen source access.
                    continue
                table = f"{column.db}.{column.table}" if column.db else aliases.get(column.table)
                name = f"{table}.{column.name}" if table else self._column_name(column)
                if allowed_columns and name not in allowed_columns:
                    raise Prompt2InsightError(
                        ErrorCode.UNAUTHORIZED_COLUMN, f"Unknown column: {name}."
                    )
                columns.add(name)
                continue
            matches = [item for item in allowed_columns if item.rsplit(".", 1)[-1] == column.name]
            if allowed_columns and len(matches) != 1:
                raise Prompt2InsightError(
                    ErrorCode.UNAUTHORIZED_COLUMN,
                    f"Column must be known and unambiguous: {column.name}.",
                )
            columns.add(matches[0] if matches else self._column_name(column))
        return frozenset(columns)

    @staticmethod
    def _is_order_projection_alias(column: exp.Column) -> bool:
        """Allow an unqualified output alias only as its SELECT block's ORDER BY key."""
        if column.table or column.db or not isinstance(column.parent, exp.Ordered):
            return False
        order = column.find_ancestor(exp.Order)
        select = column.find_ancestor(exp.Select)
        if order is None or select is None or order.find_ancestor(exp.Select) is not select:
            return False
        aliases = [
            projection.alias
            for projection in select.expressions
            if isinstance(projection, exp.Alias) and projection.alias == column.name
        ]
        if len(aliases) > 1:
            raise Prompt2InsightError(
                ErrorCode.UNAUTHORIZED_COLUMN,
                f"ORDER BY alias is ambiguous: {column.name}.",
            )
        return len(aliases) == 1

    @staticmethod
    def _function_name(function: exp.Func) -> str | None:
        if isinstance(function, (exp.And, exp.Or, exp.Not)):
            return None
        name = function.sql_name()  # type: ignore[no-untyped-call]
        return name.lower() if name else None

    def _validate_joins(
        self,
        tree: exp.Query,
        joins: list[exp.Join],
        allowed_joins: frozenset[tuple[str, str, str]],
    ) -> None:
        if not joins:
            return

        table_aliases = {
            table.alias_or_name: self._table_name(table) for table in tree.find_all(exp.Table)
        }
        for join in joins:
            join_type = (join.args.get("side") or join.args.get("kind") or "inner").lower()
            if join_type not in {"inner", "left"} or join.args.get("method") == "NATURAL":
                raise self._reject("Only declared INNER and LEFT joins are allowed.")
            on = join.args.get("on")
            if not isinstance(on, exp.EQ):
                raise self._reject("Joins must use one declared equality condition.")

            left = self._qualified_join_column(on.this, table_aliases)
            right = self._qualified_join_column(on.expression, table_aliases)
            if left is None or right is None:
                raise self._reject("Join columns must be qualified.")

            contract = (left, right, join_type)
            reverse_contract = (right, left, join_type)
            if contract not in allowed_joins and reverse_contract not in allowed_joins:
                raise Prompt2InsightError(
                    ErrorCode.UNDECLARED_JOIN, f"Undeclared join: {left} {join_type} {right}."
                )

    @staticmethod
    def _qualified_join_column(
        expression: exp.Expression,
        table_aliases: dict[str, str],
    ) -> str | None:
        if not isinstance(expression, exp.Column) or not expression.table:
            return None
        table_name: str | None
        if expression.db:
            table_name = ".".join((expression.db, expression.table))
        else:
            table_name = table_aliases.get(expression.table)
        if table_name is None:
            return None
        return f"{table_name}.{expression.name}"

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
