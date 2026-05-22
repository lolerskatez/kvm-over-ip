#!/usr/bin/env python3
"""
Helper script to generate hashed passwords for users.json
"""

import json
import getpass
from werkzeug.security import generate_password_hash
from pathlib import Path


def generate_password_hash_interactive():
    """Generate password hash interactively."""
    print("=== KVM-over-IP Password Generator ===\n")
    
    username = input("Enter username: ").strip()
    if not username:
        print("Error: Username cannot be empty")
        return
    
    password = getpass.getpass("Enter password: ").strip()
    if not password:
        print("Error: Password cannot be empty")
        return
    
    confirm = getpass.getpass("Confirm password: ").strip()
    if password != confirm:
        print("Error: Passwords do not match")
        return
    
    hashed = generate_password_hash(password)
    
    print("\n=== Generated Hash ===")
    print(f"Username: {username}")
    print(f"Hash: {hashed}\n")
    
    is_admin_input = input("Make admin? (y/n): ").strip().lower()
    is_admin = is_admin_input == 'y'
    
    user_entry = {"password": hashed, "is_admin": is_admin}
    
    print("\nAdd to /etc/kvm/users.json:")
    print(json.dumps({username: user_entry}, indent=2))
    
    save = input("\nSave to users.json? (y/n): ").strip().lower()
    if save == 'y':
        users_path = Path('/etc/kvm/users.json')
        if not users_path.exists():
            users_path = Path('./users.json')
        
        try:
            with open(users_path, 'r') as f:
                users = json.load(f)
        except FileNotFoundError:
            users = {}
        
        users[username] = user_entry
        
        with open(users_path, 'w') as f:
            json.dump(users, f, indent=2)
        
        print(f"Saved to {users_path}")


if __name__ == '__main__':
    generate_password_hash_interactive()
