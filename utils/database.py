from sqlalchemy import Column, Index, Integer, MetaData, Table, Text

metadata = MetaData()

settings = Table(
    "settings", metadata, Column("key", Text, primary_key=True), Column("value", Text, nullable=False)
)
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile", Text, nullable=False),
    Column("avatar_tag", Text),
)
media = Table(
    "media",
    metadata,
    Column("sequence", Integer, primary_key=True),
    Column("tag", Text, nullable=False, unique=True),
    Column("owner_id", Integer, nullable=False),
    Column("mimetype", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expired", Integer, nullable=False, server_default="0"),
    sqlite_autoincrement=True,
)
requests = Table(
    "requests",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Integer, nullable=False),
    Column("payload", Text, nullable=False),
)
Index("requests_user", requests.c.user_id)
messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False),
    Column("role", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("request_id", Text),
    Column("media_tag", Text),
    Column("source", Text, nullable=False),
    Column("external_id", Text, unique=True),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)
Index("messages_user", messages.c.user_id, messages.c.id)
application_types = Table(
    "application_types",
    metadata,
    Column("id", Text, primary_key=True),
    Column("label", Text(collation="NOCASE"), nullable=False, unique=True),
    Column("modes", Text, nullable=False),
    Column("created_by", Integer, nullable=False),
    Column("active", Integer, nullable=False, server_default="1"),
)
