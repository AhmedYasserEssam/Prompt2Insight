from app.domain.databases.connector import SQLDatabaseConnector
from app.domain.databases.models import SQLDialect
from app.infrastructure.databases.mysql import MySQLConnector
from app.infrastructure.databases.postgresql import PostgreSQLConnector


def create_database_connector(
    *,
    dialect: SQLDialect,
    database_url: str,
    approved_schemas: tuple[str, ...],
) -> SQLDatabaseConnector:
    match dialect:
        case SQLDialect.POSTGRES:
            return PostgreSQLConnector(database_url, approved_schemas=approved_schemas)
        case SQLDialect.MYSQL:
            return MySQLConnector(database_url, approved_schemas=approved_schemas)
