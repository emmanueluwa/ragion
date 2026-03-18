from flask import Blueprint, jsonify, request, session
from flask_login import login_required, current_user
from tasks import llm_get_state, llm_call
from celery_config import celery_app
from models import Document

chat = Blueprint("chat", __name__)


def user_has_multiple_documents():
    count = Document.query.filter_by(user_id=current_user.id, status="indexed").count()

    return count > 1


@chat.route("/get", methods=["GET", "POST"])
@login_required
def get():
    try:

        msg = request.form["msg"]
        user_input = msg.strip()
        user_id = current_user.id

        # checking to see if we are waiting for a State from the user
        if "waiting_for_state" in session and session["waiting_for_state"]:
            # state provided for previous question from user
            original_question = session.get("original_question", "")
            state_provided = user_input

            combined_query = f"{original_question} for {state_provided}"

            # saving state for future questions and clearing flags
            session["last_state"] = state_provided
            session.pop("waiting_for_state", None)
            session.pop("original_question", None)

            task = llm_call.delay(combined_query, state_provided, user_id)

            return jsonify(
                {
                    "success": True,
                    "task_id": task.id,
                    "needs_state": False,
                }
            )

        else:
            # checking if state is mentioned in new question
            state_task = llm_get_state.delay(user_input)
            state_result = state_task.get(timeout=30)

            if state_result.strip().lower() == "none":
                last_state = session.get("last_state")

                # multiple docs and no county mentioned
                if user_has_multiple_documents():
                    session["waiting_for_state"] = True
                    session["original_question"] = user_input

                    return jsonify(
                        {
                            "success": True,
                            "response": "You have multiple documents. Which jurisdiction or county does this question relate to?",
                            "needs_state": True,
                        }
                    )

                elif last_state:
                    combined_query = f"{user_input} for {last_state}"

                    task = llm_call.delay(combined_query, last_state, user_id)

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
                    session["original_question"] = user_input

                    return jsonify(
                        {
                            "success": True,
                            "response": "Which jurisdiction or county does this question relate to?",
                            "needs_state": True,
                        }
                    )

            else:
                # saving found state for future and continuing to llm query
                session["last_state"] = state_result.strip().lower()

                task = llm_call.delay(user_input, state_result, user_id)

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
