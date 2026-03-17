from flask import Blueprint, jsonify, request, session
from flask_login import login_required
from tasks import llm_get_state, llm_call
from celery_config import celery_app

chat = Blueprint("chat", __name__)


@chat.route("/get", methods=["GET", "POST"])
@login_required
def get():
    try:

        msg = request.form["msg"]
        input = msg.strip()

        # checking to see if we are waiting for a State from the user
        if "waiting_for_state" in session and session["waiting_for_state"]:
            # state provided for previous question from user
            original_question = session.get("original_question", "")
            state_provided = input

            combined_query = f"{original_question} for {state_provided}"

            # saving state for future questions and clearing flags
            session["last_state"] = state_provided
            session.pop("waiting_for_state", None)
            session.pop("original_question", None)

            task = llm_call.delay(combined_query, state_provided)

            return jsonify(
                {
                    "success": True,
                    "task_id": task.id,
                    "needs_state": False,
                }
            )

        else:
            # checking if state is mentioned in new question
            state_task = llm_get_state.delay(input)
            state_result = state_task.get(timeout=30)

            if state_result.strip().lower() == "none":
                last_state = session.get("last_state")

                if last_state:
                    combined_query = f"{input} for {last_state}"

                    task = llm_call.delay(combined_query, last_state)

                    return jsonify(
                        {
                            "success": True,
                            "task_id": task.id,
                            "needs_state": False,
                        }
                    )

                else:
                    # no state mentioned and no previous state
                    session["waiting_for_state"] = True
                    session["original_question"] = input

                    return jsonify(
                        {
                            "success": True,
                            "response": "For which county would you like to know this information?",
                            "needs_state": True,
                        }
                    )

            else:
                # saving found state for future and continuing to llm query
                session["last_state"] = state_result.strip()

                task = llm_call.delay(input, state_result)

                return jsonify(
                    {
                        "success": True,
                        "task_id": task.id,
                        "needs_state": False,
                    }
                )

    except Exception as e:
        session.pop("waiting_for_state", None)
        session.pop("original_question", None)

        return jsonify({"error": str(e)}), 500


# checking status of task
@chat.route("/check_task/<task_id>", methods=["GET"])
@login_required
def check_task(task_id):
    try:

        task = celery_app.AsyncResult(task_id)

        if task.state == "SUCCESS":
            return jsonify({"status": "SUCCESS", "response": task.result})

        elif task.state == "PENDING":
            return jsonify({"status": "PENDING"})

        elif task.state == "FAILURE":
            return jsonify({"status": "FAILURE", "error": str(task.info)})

        else:
            return jsonify({"status": task.state})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
