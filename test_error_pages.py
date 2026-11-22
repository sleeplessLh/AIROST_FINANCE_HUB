from app import app

from flask import abort


@app.route("/__test__/403")
def test_forbidden_route():
    abort(403)


@app.route("/__test__/500")
def test_server_error_route():
    raise RuntimeError("Simulated server error")


app.config["TESTING"] = True

app.config["PROPAGATE_EXCEPTIONS"] = False

with app.test_client() as client:
    response_403 = client.get("/__test__/403")
    assert response_403.status_code == 403
    assert b"Access Forbidden" in response_403.data
    print("403 Error Page Test Passed!")

    response_404 = client.get("/__test__/nonexistent")
    assert response_404.status_code == 404
    assert b"Page Not Found" in response_404.data
    print("404 Error Page Test Passed!")

    response_500 = client.get("/__test__/500")
    assert response_500.status_code == 500
    assert b"Internal Server Error" in response_500.data
    print("500 Error Page Test Passed!")

print("All Error Handling Tests Passed!")
