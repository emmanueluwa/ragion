from flask import Flask, render_template, jsonify, request

from tasks import llm_call
from celery_config import celery_app

celery_app.autodiscover_tasks(["tasks"], force=True)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


# routes
@app.route("/get", methods=["GET", "POST"])
def chat():
    try:
        msg = request.form["msg"]
        input = msg
        print(input)

        # add task to queue
        task = llm_call.delay(input)
        print("task sent :)")

        return jsonify({"success": True, "task_id": task.id})

    except Exception as e:
        print("error in endpoint:", e)
        return jsonify({"error": str(e)}), 500


# checking status of task
@app.route("/check_task/<task_id>", methods=["GET"])
def check_task(task_id):
    task = celery_app.AsyncResult(task_id)

    print(f"Task {task_id} is currently in state: {task.state}")

    if task.state == "SUCCESS":
        print(task.state)
        response = jsonify({"status": "SUCCESS", "response": task.result})
        return response

    elif task.state == "PENDING":
        print(task.state)

        return jsonify({"status": "PENDING"})

    else:
        print(task.state)

        return jsonify({"status": task.state})


# development mode
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

# if __name__ == "__main__":
#     app.run()
