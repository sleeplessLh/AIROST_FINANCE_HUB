from functools import wraps
from flask import abort, redirect, url_for
from flask_login import current_user


# 带参数的装饰器：用于检查当前用户是否拥有指定角色
def role_required(role):
    # 第一层：接收装饰器参数，例如 role_required("admin")
    # 此时 role = "admin"，并返回 decorator 函数
    def decorator(func):
        # 第二层：接收被装饰的原函数，例如 list_events
        # 此时 func 指向原本的 list_events 函数
        @wraps(func)  # 保留原函数的名称、文档字符串等元数据
        def wrapper(*args, **kwargs):
            # 第三层：用户实际调用 Route 时，才执行 wrapper
            # *args 接收位置参数，**kwargs 接收关键字参数
            # 检查用户是否已经登录
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            # 检查当前用户的角色是否符合第一层传入的 role
            if current_user.role != role:
                abort(403)
            # 通过检查后，调用原函数，并传递原本的参数
            return func(*args, **kwargs)

        # 第二层返回 wrapper 函数本身，而不是执行 wrapper()
        return wrapper

    # 第一层返回 decorator 函数本身，而不是执行 decorator()
    return decorator

# TODO 第一层接收参数 → 第二层接收原函数 → 第三层包装并执行原函数。

# ==================== 装饰器的实际执行原理 ====================


# 假设我们定义以下函数：
#* @role_required("admin")
#* def list_events():
#*     return "Event Management"


# 上面的装饰器语法，本质上等价于：
# def list_events():
#     return "Event Management"
# list_events = role_required("admin")(list_events)


# ==================== 将执行过程拆成三个步骤 ====================

# 第一步：调用 role_required("admin")
# role_required 接收 role = "admin"
# 执行 return decorator，返回内部的 decorator 函数
# decorator_function = role_required("admin")

# 第二步：调用 decorator_function(list_events)
# decorator 接收 func = 原本的 list_events 函数
# 创建 wrapper，并执行 return wrapper
# wrapped_function = decorator_function(list_events)

# 第三步：让 list_events 指向返回的 wrapper 函数
# 原本的函数对象仍然被 wrapper 内部的 func 引用保存
# list_events = wrapped_function

# 因此，之后调用 list_events()，实际调用的是 wrapper()
# wrapper 首先检查用户是否登录，再检查用户角色
# 如果检查通过，才执行 func(*args, **kwargs)
# 其中 func 指向原本的 list_events 函数


# ==================== 为什么需要三层函数？ ====================

# 第一层 role_required(role)：负责接收装饰器参数，例如 "admin"
# 第二层 decorator(func)：负责接收需要被装饰的原函数
# 第三层 wrapper(*args, **kwargs)：负责执行权限检查和原函数

# 注意：第一层和第二层通常在定义被装饰函数时执行
# 第三层则在之后实际调用被装饰函数时执行
# 由于 Closure（闭包），wrapper 仍然可以访问 role 和 func
# 即使 role_required 和 decorator 已经执行完毕，这两个变量依然可以使用
