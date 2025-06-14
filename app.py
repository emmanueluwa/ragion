import os
import uuid

from flask import Flask, render_template, jsonify, request, session
from tasks import llm_get_state, llm_call, process_file
from werkzeug.utils import secure_filename
from celery_config import celery_app

import redis

celery_app.autodiscover_tasks(["tasks"], force=True)

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY")

# redis connection for progress tracking
redis_endpoint = os.environ.get("REDIS_ENDPOINT")
redis_port = os.environ.get("REDIS_PORT", "6379")
redis_password = os.environ.get("REDIS_PASSWORD")

redis_url = f"rediss://default:{redis_password}@{redis_endpoint}:{redis_port}"
# TODO: add  ssl_cert_reqs='required' to from_url in production
r = redis.Redis.from_url(redis_url, decode_responses=True, ssl_cert_reqs=None)

# storing indexing progress by task ID
indexing_progress = {}


@app.route("/")
def index():
    return render_template("index.html")


# routes
@app.route("/get", methods=["GET", "POST"])
def chat():
    try:
        msg = request.form["msg"]
        input = msg.strip()
        print(f"User input: {input}")

        # checking to see if we are waiting for a State from the user
        if "waiting_for_state" in session and session["waiting_for_state"]:
            # state provided for previous question from user
            original_question = session.get("original_question", "")
            state_provided = input

            print(f"User provided state: {state_provided}")
            print(f"Original question: {original_question}")

            combined_query = f"{original_question} for {state_provided}"

            # saving state for future questions and clearing flags
            session["last_state"] = state_provided
            session.pop("waiting_for_state", None)
            session.pop("original_question", None)

            print(f"processing combined query: {combined_query}")

            task = llm_call.delay(combined_query, state_provided)
            return jsonify(
                {
                    "success": True,
                    "task_id": task.id,
                    "combined_query": combined_query,
                    "needs_state": False,
                }
            )

        else:
            # checking if state is mentioned in new question
            state_task = llm_get_state.delay(input)
            state_result = state_task.get(timeout=7)

            print(f"state detection result: {state_result}")

            if state_result.strip().lower() == "none":
                last_state = session.get("last_state")

                if last_state:
                    combined_query = f"{input} for {last_state}"
                    print(f"using previous state ({last_state}): {combined_query}")

                    task = llm_call.delay(combined_query, last_state)
                    return jsonify(
                        {
                            "success": True,
                            "task_id": task.id,
                            "used_previous_state": last_state,
                            "combined_query": combined_query,
                            "needs_state": False,
                        }
                    )

                else:
                    # no state mentioned and no previous state
                    session["waiting_for_state"] = True
                    session["original_question"] = input

                    print(
                        "No state mentioned and no previous state. Asking for state..."
                    )
                    return jsonify(
                        {
                            "success": True,
                            "response": "For which county would you like to know this information?",
                            "needs_state": True,
                            "original_question": input,
                        }
                    )

            else:
                # saving found state for future and continuing to llm query
                session["last_state"] = state_result.strip()
                print(f"State detected and saved: {state_result}")

                task = llm_call.delay(input, state_result)
                return jsonify(
                    {
                        "success": True,
                        "task_id": task.id,
                        "state_detected": state_result,
                        "needs_state": False,
                    }
                )

    except Exception as e:
        print(f"Error in chat endpoint: {e}")
        # clear session after error
        session.pop("waiting_for_state", None)
        session.pop("original_question", None)

        return jsonify({"error": str(e)}), 500


# checking status of task
@app.route("/check_task/<task_id>", methods=["GET"])
def check_task(task_id):
    try:
        task = celery_app.AsyncResult(task_id)
        print(f"Task {task_id} is currently in state: {task.state}")

        if task.state == "SUCCESS":
            print(task.state)
            return jsonify({"status": "SUCCESS", "response": task.result})

        elif task.state == "PENDING":
            print(task.state)
            return jsonify({"status": "PENDING"})

        elif task.state == "FAILURE":
            print(task.state)
            return jsonify({"status": "FAILURE", "error": str(task.info)})

        else:
            print(task.state)
            return jsonify({"status": task.state})

    except Exception as e:
        print(f"Error checking task: {e}")
        return jsonify({"error": str(e)}), 500


# uploading documents for chatbot reference
@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "no files added"}), 400

    file = request.files["file"]
    county = request.form.get("county", "")
    description = request.form.get("description", "")

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # making filename secure
    filename = secure_filename(file.filename)
    # saving file
    file_id = str(uuid.uuid4())
    file_path = f"data/{file_id}_{file.filename}"
    file.save(file_path)

    # start indexing task
    task = process_file.delay(file_path, file_id, county, description)

    return jsonify({"task_id": task.id, "file_id": file_id})


@app.route("/index_progress/<task_id>")
def get_index_progress(task_id):
    progress = r.hgetall(task_id)
    percent = progress.get("percent", 0)
    status = progress.get("status", "Starting...")

    return jsonify({"percent": percent, "status": status})


# development mode
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)

# if __name__ == "__main__":
#     app.run()
