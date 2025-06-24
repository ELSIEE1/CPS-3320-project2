import sys
import os
import threading
import time
import asyncio
from datetime import datetime, timezone, timedelta

import requests
from pydantic import BaseModel
from agents import Agent, Runner, function_tool

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

import tkinter as tk
from tkinter import scrolledtext, messagebox as msgbox
from plyer import notification

import re
import traceback
# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Hardcode or set via environment
HARDCODED_LLM_API_KEY = "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"
os.environ["OPENAI_API_KEY"] = HARDCODED_LLM_API_KEY

CONFIG = {
    "weather_api_key": "5bcd014903fe0036d8feff7d7dfc6c08",
    "default_location": "New York,US"
}

# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------
class Weather(BaseModel):
    city: str
    temperature_range: str
    conditions: str

@function_tool
def get_weather(location: str) -> str:
    """
    Gets the current weather for a specified location using the OpenWeatherMap API
    Args:
        location (str): City name, e.g. "London, UK".
    Returns:
        str: Description of current weather or error message.
    """
    api_key = CONFIG["weather_api_key"]
    base_url = "http://api.openweathermap.org/data/2.5/weather"
    params = {'q': location, 'appid': api_key, 'units': 'metric'}
    try:
        r = requests.get(base_url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("cod") != 200:
            return f"Could not retrieve weather for {location}. API Error: {data.get('message', '')}"
        main = data["weather"][0]["main"]
        desc = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        return f"Weather in {location}: {main} ({desc}), {temp}°C"
    except Exception as e:
        return f"Error fetching weather for {location}: {e}"

# Create the agent with the weather tool
agent = Agent(
    name="WeatherAssistant",
    instructions="You are a helpful assistant that provides weather info based on the get_weather tool.",
    tools=[get_weather],
)

# ---------------------------------------------------------------------------
# Database Models
# ---------------------------------------------------------------------------
Base = declarative_base()

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Integer, primary_key=True)
    user_input = Column(Text)
    bot_response = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Reminder(Base):
    __tablename__ = 'reminders'
    id = Column(Integer, primary_key=True)
    condition = Column(String)
    target_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    active = Column(Boolean, default=True)

# Setup database
engine = create_engine('sqlite:///app.db')
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

# ---------------------------------------------------------------------------
# Reminder Scheduler (Thread-Based)
# ---------------------------------------------------------------------------
class ReminderScheduler:
    def __init__(self, session, app, check_interval=60):
        self.session = session
        self.app = app
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add_reminder(self, condition, target_time=None):
        r = Reminder(condition=condition, target_time=target_time)
        self.session.add(r)
        self.session.commit()
        return r

    def _run(self):
        while not self._stop_event.is_set():
            
            now = datetime.now()
            print(f"[Check] now = {now}")
            session = Session()
            due = session.query(Reminder).filter(
                Reminder.active == True,
                Reminder.target_time != None,
                Reminder.target_time <= now
            ).all()

            print(f"[Check] Found {len(due)} due reminders.")

            for r in due:
                print(f"[Check] reminder = {r.target_time}")
                print(f"[Check] is due = {r.target_time <= now}")
                print(f"Triggering reminder: {r.condition}")
                try:
                    notification.notify(
                        title="Weather Reminder",
                        message=f"Reminder: {r.condition}",
                        timeout=10
                    )
                except Exception as e:
                    print (f"Desktop notification failed: {e}")
                
                    try:
                        self.app.after(0, lambda msg=r.condition: self.app.show_reminder_popup(msg))
                    except Exception as e:
                        print(f"Tkinter popup scheduling failed: {e}")

                r.active = False
                session.commit()
            session.close()
            time.sleep(self.check_interval)

    def shutdown(self):
        self._stop_event.set()
        self.thread.join()

# ---------------------------------------------------------------------------
# AI Backend using openai-agents
# ---------------------------------------------------------------------------
class AIBackend:
    def __init__(self):
        pass

    def process_input(self, text: str) -> str:
        try:
            # Run the agent synchronously
            result = asyncio.run(Runner.run(agent, input=text))
            return result.final_output
        except Exception as e:
            return f"Agent error: {e}"

# ---------------------------------------------------------------------------
# Main Application GUI using tkinter
# ---------------------------------------------------------------------------
class ChatBotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Weather & Reminder Assistant")
        self.geometry("600x800")
        self.session = Session()
        self.ai = AIBackend()
        self.scheduler = ReminderScheduler(self.session, self) 

        # Chat view
        self.chat_view = scrolledtext.ScrolledText(self, state='disabled')
        self.chat_view.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Input frame
        frm = tk.Frame(self)
        self.input_line = tk.Entry(frm)
        self.input_line.bind('<Return>', lambda e: self.on_send())
        self.input_line.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn = tk.Button(frm, text="Send", command=self.on_send)
        btn.pack(side=tk.LEFT, padx=5)
        frm.pack(fill=tk.X, padx=5, pady=5)

        # Reminders list
        reminder_frame = tk.Frame(self)
        reminder_frame.pack(fill=tk.X, padx=5, pady=(0, 10))

        tk.Label(reminder_frame, text="Active Reminders:").pack(anchor='w')

        self.rem_list = tk.Listbox(reminder_frame)
        self.rem_list.pack(fill=tk.X, pady=(0, 5))

        #clear reminder
        clear_btn = tk.Button(reminder_frame, text="Clear All Reminders", command=self.clear_all_reminders)
        clear_btn.pack(anchor='e', padx=5)

        reminder_frame.update_idletasks()

        self.refresh_reminders()

        future_time = datetime.now() + timedelta(seconds=30)
        test_reminder = self.scheduler.add_reminder("Test reminder at 30s", target_time=future_time)
        print(f"Test reminder set for {future_time}")

    def append_chat(self, sender: str, text: str):
        ts = datetime.now().strftime('%H:%M')
        self.chat_view.configure(state='normal')
        self.chat_view.insert(tk.END, f"[{sender} {ts}]: {text}\n")
        self.chat_view.configure(state='disabled')
        self.chat_view.see(tk.END)

    def refresh_reminders(self):
        self.rem_list.delete(0, tk.END)
        for r in self.session.query(Reminder).filter_by(active=True):
            ts = r.target_time.strftime('%Y-%m-%d %H:%M') if r.target_time else "On Condition"
            self.rem_list.insert(tk.END, f"{r.id}: {r.condition} @ {ts}")

    def on_send(self):
        txt = self.input_line.get().strip()
        if not txt:
            return
        self.append_chat("User", txt)
        conv = Conversation(user_input=txt)
        self.session.add(conv)
        self.session.commit()

        threading.Thread(target=self.handle_user, args=(txt, conv.id), daemon=True).start()
        self.input_line.delete(0, tk.END)
    def on_close(self):
        self.scheduler.shutdown()
        self.destroy()

    def handle_user(self, text: str, conv_id: int):
        if text.lower().startswith("remind me"):
            r = self.scheduler.add_reminder(condition=text)
            resp = f"Reminder #{r.id} created: {r.condition}"
        else:
            resp = self.ai.process_input(text)
#
        session = Session()
        conv = session.query(Conversation).get(conv_id)
        conv.bot_response = resp
        session.commit()
        session.close()

        self.append_chat("Bot", resp)
        self.refresh_reminders()

        m = re.search(r"weather in\s*([A-Za-z ,]+)", text, re.IGNORECASE)
    def clear_all_reminders(self):
        if msgbox.askyesno("Clear All", "Are you sure you want to delete all reminders?"):
            self.session.query(Reminder).delete()
            self.session.commit()
            self.refresh_reminders()
            msgbox.showinfo("Cleared", "All reminders have been deleted.")
    
    def show_reminder_popup(self, msg: str):
        msgbox.showinfo("Reminder", msg)
        
if __name__ == '__main__':
    app = ChatBotApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
