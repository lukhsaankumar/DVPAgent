from dotenv import load_dotenv
import os
import sys


def main():
    load_dotenv()
    uri = os.getenv('SUPABASE_URI') or os.getenv('SUPABASE_URL')
    if not uri:
        print('MISSING_SUPABASE_URI')
        sys.exit(2)

    # If the .env line was like SUPABASE_URI-postgresql://..., allow either form
    if uri.startswith('postgresql://') or uri.startswith('postgres://'):
        pg_uri = uri
    else:
        # Try to extract a postgresql:// substring
        if 'postgresql://' in uri:
            pg_uri = uri[uri.index('postgresql://'):]
        elif 'postgres://' in uri:
            pg_uri = uri[uri.index('postgres://'):]
        else:
            print('INVALID_SUPABASE_URI')
            sys.exit(3)

    sql_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sql', 'schema.sql')
    if not os.path.exists(sql_path):
        print('MISSING_SQL_FILE')
        sys.exit(4)

    sql = open(sql_path, 'r', encoding='utf-8').read()

    # Try to import psycopg, but on some Windows installs the binary exposes
    # as `psycopg_binary`. Accept either and alias to `psycopg` for usage.
    try:
        import psycopg as _psycopg
    except Exception:
        try:
            import psycopg_binary as _psycopg
        except Exception as e:
            print('MISSING_PG_DRIVER')
            print(e)
            sys.exit(5)
    psycopg = _psycopg

    try:
        # psycopg 3 uses connect(), context managers work with it
        with psycopg.connect(pg_uri, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                print('SCHEMA_APPLIED')
    except Exception as e:
        print('SCHEMA_ERROR')
        print(e)
        sys.exit(6)


if __name__ == '__main__':
    main()
