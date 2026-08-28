"""Apply migrations/*.sql in filename order.

Tracks applied files in a `schema_migrations` table; each file runs inside a
transaction. Usage: python -m scripts.migrate
"""
