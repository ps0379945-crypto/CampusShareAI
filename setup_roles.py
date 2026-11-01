
import sqlite3
import os

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "campusshare.db"
)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

email = input("Enter the registered email: ").strip().lower()

print("\n1. Admin")
print("2. Resource Owner")

choice = input("Choose role (1 or 2): ")

if choice == "1":
    role = "Admin"
elif choice == "2":
    role = "Resource Owner"
else:
    print("Invalid choice.")
    conn.close()
    raise SystemExit

cursor.execute(
    "UPDATE users SET role = ? WHERE LOWER(email) = ?",
    (role, email)
)

if cursor.rowcount > 0:
    conn.commit()
    print(f"Success! Account role updated to {role}.")
else:
    print("No registered account found with that email.")

conn.close()
