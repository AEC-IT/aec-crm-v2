from app import create_app
from app.seed import seed

app = create_app()
with app.app_context():
    seed()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
