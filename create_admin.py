from werkzeug.security import generate_password_hash # Or from your models if it's encapsulated there
# If your User model handles hashing directly (e.g., user.set_password()), you can use that:
# from models import User # Assuming User.set_password uses generate_password_hash

def generate_hash(password):
    # This uses werkzeug directly, which is what Flask-Login often uses.
    # Make sure this is the SAME hashing algorithm your User.check_password() uses.
    # If your User model has a set_password method that does this, it's better to use that
    # to ensure consistency. For example:
    # temp_user = User(id=None, username=None, role=None)
    # temp_user.set_password(password)
    # return temp_user.password_hash
    return generate_password_hash(password, method='pbkdf2:sha256') # A common method

if __name__ == '__main__':
    admin_username = "admin"
    admin_password = input(f"Enter password for admin user '{admin_username}': ")
    
    hashed_password = generate_hash(admin_password)
    
    print(f"\nUsername: {admin_username}")
    print(f"Hashed Password: {hashed_password}")
    print(f"Role: admin")
    
    print("\nSQL INSERT statement (copy and run this in phpMyAdmin or MySQL client):")
    sql_statement = f"""
    INSERT INTO `users` (`Username`, `Password`, `Role`)
    VALUES ('{admin_username}', '{hashed_password}', 'admin');
    """
    print(sql_statement)

    # Optional: Directly insert if your DB config is accessible and models.py has User.create
    # This is more robust as it uses your defined User model's creation logic.
    # from models import User, DB_CONFIG # Assuming DB_CONFIG is in models or accessible
    # import mysql.connector

    # try:
    #     # Attempt direct creation via model (safer if User.create handles hashing and unique checks)
    #     print("\nAttempting to create user directly via model...")
    #     # This uses the User.create method defined in your models.py
    #     # User.create should handle the password hashing internally.
    #     # We call it with the plain password.
    #     success, msg_or_id = User.create(username=admin_username, password=admin_password, role='admin')
    #     if success:
    #         print(f"Admin user '{admin_username}' created successfully with UserID: {msg_or_id} via model.")
    #     else:
    #         print(f"Failed to create admin user via model: {msg_or_id}")
    # except ImportError:
    #     print("Could not import User model for direct creation. Use the SQL statement above.")
    # except Exception as e:
    #     print(f"Error during direct creation attempt: {e}")