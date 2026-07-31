import os
import time
from datetime import datetime
from typing import List
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Text, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from passlib.context import CryptContext
import jwt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import aiosmtplib

# 1. DATABASE CONFIGURATION (MySQL / MariaDB via environment variables)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("CRITICAL ERROR: DATABASE_URL environment variable is not set!")

engine = create_engine(DATABASE_URL, pool_recycle=3600, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. SECURITY & EMAIL CONFIGURATIONS (Loaded exclusively from host environment)
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALLOWED_FRONTEND = os.getenv("ALLOWED_FRONTEND_URL", "*")

# Secure SMTP Mail Variables (Hidden from codebase)
SMTP_HOST = os.getenv("SMTP_HOST")          # e.g., ://gmail.com
SMTP_PORT = int(os.getenv("SMTP_PORT", 587)) # e.g., 587 (TLS) or 465 (SSL)
SMTP_USER = os.getenv("SMTP_USER")          # The hidden sending email account
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")  # The hidden account app-password
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")      # The hidden recipient admin inbox

if not SECRET_KEY:
    raise RuntimeError("CRITICAL ERROR: JWT_SECRET_KEY environment variable is not set!")

ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# 3. DATABASE MODELS
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(150), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="user")

class TicketDB(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True, index=True)
    app_name = Column(String(150), nullable=False)
    device = Column(String(150), nullable=False)
    description = Column(Text, nullable=False)
    user_id = Column(Integer, nullable=False)
    timestamp = Column(String(50), nullable=False)
    status = Column(String(50), default="active")

class NewsDB(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)

def init_db():
    print("Connecting to remote database and verifying tables...")
    for i in range(5):
        try:
            Base.metadata.create_all(bind=engine)
            print("Database connected and structural check complete.")
            return
        except OperationalError:
            print(f"Database connection waiting... retry {i+1}/5")
            time.sleep(3)
    raise RuntimeError("Could not connect to the remote MySQL database.")

init_db()

# 4. INITIALIZE APP & CORS RULES
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_FRONTEND], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def seed_database():
    db = SessionLocal()
    try:
        if not db.query(UserDB).filter(UserDB.username == "admin").first():
            db.add(UserDB(username="admin", hashed_password=pwd_context.hash("admin123"), role="admin"))
            db.add(NewsDB(title="System Operational", content="Securely integrated live asynchronous email system."))
            db.commit()
    finally:
        db.close()

seed_database()

# 5. ASYNCHRONOUS SECURE EMAIL UTILITY (Runs silently in backend thread pool)
async def send_admin_notification_email(ticket_id: int, username: str, app_name: str, device: str, description: str, timestamp: str):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ADMIN_EMAIL]):
        print("[MAIL SYSTEM WARNING]: Email variables missing in Render dashboard. Notification skipped.")
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = f"🚨 New Incident Ticket #{ticket_id} Filed"
    message["From"] = SMTP_USER
    message["To"] = ADMIN_EMAIL

    html_content = f"""
    <html>
    <body style="font-family: sans-serif; background-color: #f7fafc; padding: 20px; margin: 0;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 25px; border-radius: 8px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #e53e3e; margin-top: 0; font-size: 1.4rem;">Alert: New Issue Ticket Created</h2>
            <p style="color: #4a5568; font-size: 1rem;">An operator has submitted a new problem ticket via the application portal.</p>
            <hr style="border: 0; border-top: 1px solid #edf2f7; margin: 20px 0;">
            <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.95rem;">
                <tr><th style="padding: 6px 0; color: #718096; width: 120px;">Ticket ID:</th><td style="padding: 6px 0; font-weight: bold;">#{ticket_id}</td></tr>
                <tr><th style="padding: 6px 0; color: #718096;">Operator:</th><td style="padding: 6px 0;">{username}</td></tr>
                <tr><th style="padding: 6px 0; color: #718096;">Application:</th><td style="padding: 6px 0; font-weight: bold; color: #2d3748;">{app_name}</td></tr>
                <tr><th style="padding: 6px 0; color: #718096;">Device Setup:</th><td style="padding: 6px 0;">{device}</td></tr>
                <tr><th style="padding: 6px 0; color: #718096;">Timestamp:</th><td style="padding: 6px 0; color: #718096;">{timestamp}</td></tr>
            </table>
            <div style="background-color: #f7fafc; border-left: 4px solid #4a5568; padding: 15px; margin-top: 20px; border-radius: 4px;">
                <b style="color: #4a5568; font-size: 0.9rem; display: block; margin-bottom: 5px;">Description:</b>
                <p style="margin: 0; color: #2d3748; line-height: 1.5; white-space: pre-wrap;">{description}</p>
            </div>
            <hr style="border: 0; border-top: 1px solid #edf2f7; margin: 25px 0;">
            <p style="font-size: 0.85rem; color: #a0aec0; text-align: center; margin: 0;">This is an automated system notification from your secure cloud environment.</p>
        </div>
    </body>
    </html>
    """
    message.attach(MIMEText(html_content, "html"))

    try:
        use_tls = (SMTP_PORT == 465)
        smtp = aiosmtplib.SMTP(hostname=SMTP_HOST, port=SMTP_PORT, use_tls=use_tls)
        await smtp.connect()
        
        if SMTP_PORT == 587:
            await smtp.starttls()
            
        await smtp.login(SMTP_USER, SMTP_PASSWORD)
        await smtp.send_message(message)
        await smtp.quit()
        print(f"[MAIL SYSTEM SUCCESS]: Alert email for Ticket #{ticket_id} sent to admin inbox.")
    except Exception as e:
        print(f"[MAIL SYSTEM ERROR]: Failed to route email notification: {str(e)}")

# 6. AUTHORIZATION MECHANISMS
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(status_code=401, detail="Invalid session token.")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    user = db.query(UserDB).filter(UserDB.username == username).first()
    if user is None:
        raise credentials_exception
    return user

def require_admin(user: UserDB = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied: Administrator privileges required.")
    return user

# 7. ROUTE ENDPOINTS
@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == form_data.username).first()
    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect credentials.")
    token = jwt.encode({"sub": user.username}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer", "role": user.role}

@app.put("/user/update-credentials")
async def update_credentials(data: dict, current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    if "username" in data and data["username"]:
        existing = db.query(UserDB).filter(UserDB.username == data["username"]).first()
        if existing and existing.id != current_user.id:
            raise HTTPException(status_code=400, detail="Username is already taken.")
        current_user.username = data["username"]
    if "password" in data and data["password"]:
        current_user.hashed_password = pwd_context.hash(data["password"])
    db.commit()
    return {"status": "success"}

# --- ADMIN USER CRUD ---
@app.post("/admin/users", dependencies=[Depends(require_admin)])
async def admin_create_user(data: dict, db: Session = Depends(get_db)):
    if db.query(UserDB).filter(UserDB.username == data["username"]).first():
        raise HTTPException(status_code=400, detail="User already exists.")
    new_user = UserDB(
        username=data["username"],
        hashed_password=pwd_context.hash(data["password"]),
        role=data.get("role", "user")
    )
    db.add(new_user)
    db.commit()
    return {"status": "success"}

@app.get("/admin/users", dependencies=[Depends(require_admin)])
async def admin_list_users(db: Session = Depends(get_db)):
    return db.query(UserDB).all()

@app.delete("/admin/users/{user_id}", dependencies=[Depends(require_admin)])
async def admin_delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user:
        db.delete(user)
        db.commit()
        return {"status": "success"}
    
#--- TICKETS LOG WITH BACKGROUND EMAIL ACTIONS ---

@app.post("/tickets")
async def create_ticket(data: dict, bg_tasks: BackgroundTasks, current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ticket = TicketDB(app_name=data["app_name"],device=data["device"],description=data["description"],user_id=current_user.id,timestamp=now,status="active")
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    bg_tasks.add_task(send_admin_notification_email,ticket_id=ticket.id,username=current_user.username,app_name=ticket.app_name,device=ticket.device,description=ticket.description,timestamp=ticket.timestamp)
    return {"status": "success"}

@app.get("/admin/tickets", dependencies=[Depends(require_admin)])
async def list_all_tickets(db: Session = Depends(get_db)):
    return db.query(TicketDB).all()

@app.put("/admin/tickets/{ticket_id}/close", dependencies=[Depends(require_admin)])
async def close_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(TicketDB).filter(TicketDB.id == ticket_id).first()
    if not ticket:raise HTTPException(status_code=404, detail="Ticket record not found.")
    ticket.status = "closed"
    db.commit()
    return {"status": "success"}

#--- NEWS SYSTEM ---
@app.post("/admin/news", dependencies=[Depends(require_admin)])
async def create_news(data: dict, db: Session = Depends(get_db)):
    news = NewsDB(title=data["title"], content=data["content"])
    db.add(news)
    db.commit()
    return {"status": "success"}

@app.get("/news")
async def get_recent_news(current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(NewsDB).order_by(NewsDB.id.desc()).limit(10).all()