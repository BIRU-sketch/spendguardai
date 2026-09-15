import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client, Client
supabase : Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
app = Flask(__name__)


@app.route("/")
def home():
    return render_template('home.html')
@app.route("/dashboard")
def dashboard():
    access_token = request.cookies.get('access_token')
    if not access_token:
        return redirect(url_for('auth'))

    try:
        user_response = supabase.auth.get_user(access_token)
        if not user_response.user:
            return redirect(url_for('auth'))
    except Exception:
        app.logger.exception("Dashboard authentication failed")
        return redirect(url_for('auth'))

    return render_template('dashboard.html', user=user_response.user.user_metadata.get('name', 'user'))
@app.route("/auth")
def auth():
    return render_template('auth.html')

@app.route("/auth/signin", methods=['POST'])
def signin():
    email = request.form.get('email')
    password = request.form.get('password')
    try:
        response = supabase.auth.sign_in_with_password(
            {
                "email": email,
                "password": password
            }
        )
        if response.session is None:
            return jsonify({"error": "Sign-in did not return a session"}), 401

        result = redirect(url_for('dashboard'))
        result.set_cookie(
            'access_token',
            response.session.access_token,
            httponly=True,
            samesite='Lax'
        )
        return result
    except Exception:
        app.logger.exception("Sign-in failed for %s", email)
        return jsonify({"error": "Invalid credentials"}), 401

@app.route("/auth/signup", methods=['POST'])
def signup():
    signup_data = request.get_json(silent=True) or {}
    name = signup_data.get('name')
    email = signup_data.get('email')
    password = signup_data.get('password')
    confirm_password = signup_data.get('confirm_password')
    if not password == confirm_password:
        return jsonify({"error": "Passwords do not match"}), 400
    try:
        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"name": name}}
        })
        session = response.session

        if session is None:
            return jsonify({
                "status": "pending_confirmation",
                "message": "Signup succeeded. Check your email to confirm your account."
            }), 202

        result = jsonify({"status": "success"})
        result.set_cookie(
            'access_token',
            session.access_token,
            httponly=True,
            samesite='Lax'
        )
        return result
    except Exception as e:
        app.logger.exception("Signup failed for %s", email)
        return jsonify({"error": "Signup failed"}), 500
if __name__ == "__main__":
    app.run(debug=True)