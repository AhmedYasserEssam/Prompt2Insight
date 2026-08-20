from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SQLDialect

DANGEROUS_FUNCTIONS = frozenset(
    {
        "benchmark",
        "dblink",
        "dblink_exec",
        "load_file",
        "lo_export",
        "lo_import",
        "pg_advisory_lock",
        "pg_advisory_lock_shared",
        "pg_cancel_backend",
        "pg_create_restore_point",
        "pg_file_rename",
        "pg_file_unlink",
        "pg_file_write",
        "pg_log_backend_memory_contexts",
        "pg_ls_dir",
        "pg_notify",
        "pg_promote",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_sleep",
        "pg_stat_file",
        "pg_terminate_backend",
        "pg_try_advisory_lock",
        "pg_try_advisory_lock_shared",
        "set_config",
        "sleep",
        "sys_eval",
        "sys_exec",
    }
)


@dataclass(frozen=True, slots=True)
class SQLPolicy:
    allowed_tables: frozenset[str]
    allowed_columns: frozenset[str] = frozenset()
    prohibited_functions: frozenset[str] = DANGEROUS_FUNCTIONS
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

        columns = self._canonical_columns(
            tree, policy.allowed_columns, cte_names, dialect
        )

        functions = {
            name
            for function in tree.find_all(exp.Func)
            if (name := self._function_name(function)) is not None
        }
        blocked = functions & policy.prohibited_functions
        if blocked:
            raise self._reject(
                f"Dangerous functions are not allowed: {', '.join(sorted(blocked))}."
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
        dialect: SQLDialect,
    ) -> frozenset[str]:
        aliases = {
            table.alias_or_name: self._table_name(table) for table in tree.find_all(exp.Table)
        }
        columns: set[str] = set()
        for column in tree.find_all(exp.Column):
            if self._is_valid_projection_alias_reference(column, dialect):
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
    def _is_valid_projection_alias_reference(
        column: exp.Column, dialect: SQLDialect
    ) -> bool:
        """Allow unique output aliases in this SELECT block's supported alias contexts."""
        if column.table or column.db or dialect not in {
            SQLDialect.POSTGRES,
            SQLDialect.MYSQL,
        }:
            return False

        select = column.find_ancestor(exp.Select)
        if select is None:
            return False

        context: str | None = None
        if isinstance(column.parent, exp.Ordered):
            order = column.parent.parent
            if isinstance(order, exp.Order) and select.args.get("order") is order:
                context = "ORDER BY"
        elif isinstance(column.parent, exp.Group) and select.args.get("group") is column.parent:
            context = "GROUP BY"

        if context is None:
            return False

        aliases = [
            projection.alias
            for projection in select.expressions
            if isinstance(projection, exp.Alias) and projection.alias == column.name
        ]
        if len(aliases) > 1:
            raise Prompt2InsightError(
                ErrorCode.UNAUTHORIZED_COLUMN,
                f"{context} alias is ambiguous: {column.name}.",
            )
        return len(aliases) == 1

    @staticmethod
    def _function_name(function: exp.Func) -> str | None:
        if isinstance(function, (exp.And, exp.Or, exp.Not)):
            return None
        if isinstance(function, exp.Anonymous):
            return function.name.lower()
        name = function.sql_name()  # type: ignore[no-untyped-call]
        return name.lower() if name else None

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
