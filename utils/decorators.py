from functools import wraps
from flask import abort, redirect, url_for
from flask_login import current_user


# Decorator: allow the route only for users with the given role (403 otherwise).
def role_required(role):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role != role:
                abort(403)
            return func(*args, **kwargs)

        return wrapper

    return decorator
