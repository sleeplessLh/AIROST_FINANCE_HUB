# 导入 Flask Application
from app import app

# abort：主动产生指定 HTTP Error
from flask import abort


# 测试用的临时 URL，避免与真实业务 Route 冲突
@app.route("/__test__/403")
def test_forbidden_route():
    abort(403)


# 测试用的临时 URL，模拟服务器异常
@app.route("/__test__/500")
def test_server_error_route():
    raise RuntimeError("Simulated server error")


# 只在当前测试程序中设置测试配置
app.config["TESTING"] = True

# False：让异常经过 Flask Error Handler
app.config["PROPAGATE_EXCEPTIONS"] = False

# 使用 Test Client 发送模拟请求
with app.test_client() as client:
    # 测试 403 Forbidden
    response_403 = client.get("/__test__/403")
    assert response_403.status_code == 403
    assert b"Access Forbidden" in response_403.data
    print("403 Error Page Test Passed!")

    # 测试 404 Not Found
    response_404 = client.get("/__test__/nonexistent")
    assert response_404.status_code == 404
    assert b"Page Not Found" in response_404.data
    print("404 Error Page Test Passed!")

    # 测试 500 Internal Server Error
    response_500 = client.get("/__test__/500")
    assert response_500.status_code == 500
    assert b"Internal Server Error" in response_500.data
    print("500 Error Page Test Passed!")

print("All Error Handling Tests Passed!")
