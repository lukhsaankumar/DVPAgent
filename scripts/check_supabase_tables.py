from dotenv import load_dotenv
import os
import sys
import urllib.request


def main():
    load_dotenv()
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    PUBLISHABLE = os.getenv('SUPABASE_PUBLISHABLE_KEY')
    SECRET = os.getenv('SUPABASE_SECRET_KEY')

    if not SUPABASE_URL or not SECRET:
        print('MISSING_KEYS')
        sys.exit(2)

    base = SUPABASE_URL.rstrip('/')
    tables = [
        'salesforce_data',
        'tableau_data',
        'consultant_scorecard_data',
        'consultant_scorecard_raw',
        'consultant_scorecard_monthly',
        'consultant_scorecard_metric',
        'meeting_prep_documents',
    ]

    for t in tables:
        url = f"{base}/{t}?select=*&limit=1"

        # Try 1: Authorization Bearer SECRET + apikey=publishable
        headers1 = {
            'apikey': PUBLISHABLE or '',
            'Authorization': f'Bearer {SECRET}',
            'Accept': 'application/json',
        }

        # Try 2: use service key in apikey header only (no Authorization)
        headers2 = {
            'apikey': SECRET,
            'Accept': 'application/json',
        }

        def try_request(headers):
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode('utf-8')
                return resp.getcode(), body

        try:
            code, body = try_request(headers1)
            print(f"{t}: OK (method1) {code} - sample: {body[:200]}")
            continue
        except urllib.error.HTTPError as e:
            err = ''
            try:
                err = e.read().decode('utf-8')
            except Exception:
                pass
            print(f"{t}: method1 HTTPError {e.code} - {e.reason} - {err}")

        try:
            code, body = try_request(headers2)
            print(f"{t}: OK (method2) {code} - sample: {body[:200]}")
            continue
        except urllib.error.HTTPError as e:
            err = ''
            try:
                err = e.read().decode('utf-8')
            except Exception:
                pass
            print(f"{t}: method2 HTTPError {e.code} - {e.reason} - {err}")
        except Exception as e:
            print(f"{t}: ERROR {e}")


if __name__ == '__main__':
    main()
