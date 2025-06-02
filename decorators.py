# decorators.py
from functools import wraps
from flask import abort
from flask_login import current_user

def role_required(role_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return abort(401) # Unauthorized
            if current_user.role != role_name and (isinstance(role_name, list) and current_user.role not in role_name):
                 # If role_name is a list, check if user's role is in that list
                 # If role_name is a string, check for exact match
                if not (isinstance(role_name, list) and current_user.role in role_name):
                     return abort(403) # Forbidden
            return f(*args, **kwargs)
        return decorated_function
    return decorator

admin_required = role_required('admin')
manager_required = role_required('manager')
tester_required = role_required('tester')
manager_or_admin_required = role_required(['admin', 'manager'])