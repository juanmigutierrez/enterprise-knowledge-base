"""Mock HR database: a stand-in for an operational Postgres read with Debezium.

Applications write rows; nobody sends events. The database itself keeps a
change log (Postgres's write-ahead log, read by Debezium), and change data
capture (CDC) reads that log: every insert, update and delete, deletes included,
in commit order, with the row before and after.

Here SQLite triggers write that log into a ``cdc_log`` table, in Debezium's
vocabulary (``op`` = r / c / u / d, ``before`` / ``after``). It is a file, not
a service: the product reads it with SQL, as a CDC reader reads a database.
"""
