
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.database import engine

def check():
    with engine.connect() as conn:
        for table in ["orders", "sub_orders", "order_items"]:
            print(f"\n--- {table} ---")
            res = conn.execute(text(f"DESC {table}"))
            for row in res:
                print(row[0], row[1])

if __name__ == "__main__":
    check()
