from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime
import difflib
import re
from typing import List, Dict, Tuple
import os
from dotenv import load_dotenv
import logging
import json
from bson.objectid import ObjectId
import threading
import time
from queue import Queue
import base64
import uuid
from werkzeug.utils import secure_filename

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configurations for uploads
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# MongoDB connection
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
DB_NAME = "simhastha_milaap"

# Connect to MongoDB
try:
    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    person_reports = db['person_reports']
    item_reports = db['item_reports']
    notifications = db['notifications']
    photos = db['photos']
    print("✅ Connected to MongoDB successfully!")
except Exception as e:
    print(f"❌ Error connecting to MongoDB: {e}")

# Notification queue for background processing
notification_queue = Queue()

# AI-powered matching algorithms
class SmartMatcher:
    @staticmethod
    def calculate_similarity(str1: str, str2: str) -> float:
        return difflib.SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
    
    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        common_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'was', 'are', 'were'}
        words = re.findall(r'\b\w+\b', text.lower())
        return [word for word in words if word not in common_words and len(word) > 2]
    
    @staticmethod
    def match_persons(new_person: Dict, existing_persons: List[Dict]) -> List[Tuple[Dict, float]]:
        matches = []
        
        for existing in existing_persons:
            if new_person['report_type'] == existing['report_type']:
                continue
                
            score = 0.0
            factors = 0
            
            # Name similarity
            if new_person.get('name') and existing.get('name'):
                name_sim = SmartMatcher.calculate_similarity(new_person['name'], existing['name'])
                if name_sim > 0.6:
                    score += name_sim * 0.4
                    factors += 1
            
            # Age matching
            if new_person.get('age') and existing.get('age'):
                try:
                    age_diff = abs(int(new_person['age']) - int(existing['age']))
                    if age_diff <= 5:
                        age_score = 1.0 - (age_diff * 0.1)
                        score += age_score * 0.2
                        factors += 1
                except:
                    pass
            
            # Gender matching
            if new_person.get('gender') and existing.get('gender'):
                if new_person['gender'].lower() == existing['gender'].lower():
                    score += 0.2
                    factors += 1
            
            # Description keyword matching
            if new_person.get('description') and existing.get('description'):
                new_keywords = SmartMatcher.extract_keywords(new_person['description'])
                existing_keywords = SmartMatcher.extract_keywords(existing['description'])
                
                if new_keywords and existing_keywords:
                    keyword_matches = 0
                    for new_kw in new_keywords:
                        for existing_kw in existing_keywords:
                            if SmartMatcher.calculate_similarity(new_kw, existing_kw) > 0.7:
                                keyword_matches += 1
                                break
                    
                    if len(new_keywords) > 0:
                        desc_score = keyword_matches / len(new_keywords)
                        score += desc_score * 0.2
                        factors += 1
            
            # Location similarity
            if new_person.get('location') and existing.get('location'):
                location_sim = SmartMatcher.calculate_similarity(new_person['location'], existing['location'])
                if location_sim > 0.4:
                    score += location_sim * 0.2
                    factors += 1
            
            if factors > 0:
                final_score = score / factors
                if final_score > 0.55:
                    matches.append((existing, final_score))
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:5]
    
    @staticmethod
    def match_items(new_item: Dict, existing_items: List[Dict]) -> List[Tuple[Dict, float]]:
        matches = []
        
        for existing in existing_items:
            if new_item['report_type'] == existing['report_type']:
                continue
                
            score = 0.0
            factors = 0
            
            # Category similarity
            if new_item.get('category') and existing.get('category'):
                category_sim = SmartMatcher.calculate_similarity(new_item['category'], existing['category'])
                if category_sim > 0.6:
                    score += category_sim * 0.25
                    factors += 1
            
            # Color similarity
            if new_item.get('color') and existing.get('color'):
                color_sim = SmartMatcher.calculate_similarity(new_item['color'], existing['color'])
                if color_sim > 0.6:
                    score += color_sim * 0.25
                    factors += 1
            
            # Brand/Model similarity
            if new_item.get('brand') and existing.get('brand'):
                brand_sim = SmartMatcher.calculate_similarity(new_item['brand'], existing['brand'])
                if brand_sim > 0.6:
                    score += brand_sim * 0.2
                    factors += 1
            
            # Description matching
            if new_item.get('description') and existing.get('description'):
                desc_keywords_new = SmartMatcher.extract_keywords(new_item['description'])
                desc_keywords_existing = SmartMatcher.extract_keywords(existing['description'])
                
                if desc_keywords_new and desc_keywords_existing:
                    keyword_matches = 0
                    for new_kw in desc_keywords_new:
                        for existing_kw in desc_keywords_existing:
                            if SmartMatcher.calculate_similarity(new_kw, existing_kw) > 0.7:
                                keyword_matches += 1
                                break
                    
                    if len(desc_keywords_new) > 0:
                        desc_score = keyword_matches / len(desc_keywords_new)
                        score += desc_score * 0.15
                        factors += 1
            
            # Location proximity
            if new_item.get('location') and existing.get('location'):
                location_sim = SmartMatcher.calculate_similarity(new_item['location'], existing['location'])
                if location_sim > 0.4:
                    score += location_sim * 0.1
                    factors += 1
            
            # Calculate final score
            if factors > 0:
                final_score = score / factors
                if final_score > 0.55:
                    matches.append((existing, final_score))
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:5]

# WhatsApp Handler with Twilio Integration
class WhatsAppHandler:
    @staticmethod
    def send_whatsapp_message(to_number, message_body):
        """Send WhatsApp message using Twilio"""
        try:
            # Check if Twilio is configured
            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            auth_token = os.getenv('TWILIO_AUTH_TOKEN')
            twilio_number = os.getenv('TWILIO_WHATSAPP_NUMBER')
            
            if not all([account_sid, auth_token, twilio_number]):
                print(f"📱 WhatsApp simulation to {to_number}:\n{message_body}")
                return True
            
            from twilio.rest import Client
            
            if not to_number.startswith('whatsapp:'):
                to_number = f'whatsapp:{to_number}'
            
            if not twilio_number.startswith('whatsapp:'):
                twilio_number = f'whatsapp:{twilio_number}'
            
            client = Client(account_sid, auth_token)
            message = client.messages.create(
                body=message_body,
                from_=twilio_number,
                to=to_number
            )
            
            print(f"✅ WhatsApp message sent to {to_number}, SID: {message.sid}")
            return True
            
        except Exception as e:
            print(f"❌ Error sending WhatsApp message: {e}")
            print(f"📱 WhatsApp simulation to {to_number}:\n{message_body}")
            return False
    
    @staticmethod
    def process_incoming_message(from_number, message_body):
        """Process incoming WhatsApp messages"""
        try:
            print(f"📩 Received from {from_number}: {message_body}")
            
            notifications.insert_one({
                'type': 'incoming',
                'contact_number': from_number,
                'message': message_body,
                'received_at': datetime.now(),
                'status': 'processed'
            })
            
            message_body = message_body.strip().lower()
            response = ""
            
            if any(word in message_body for word in ['hello', 'hi', 'hey', 'namaste']):
                response = "Namaste! Welcome to Simhastha Milaap Lost & Found System. 🙏\n\nHow can we help you today?\n1. Report missing person - Type 'MISSING'\n2. Report found person - Type 'FOUND PERSON'\n3. Report lost item - Type 'LOST'\n4. Report found item - Type 'FOUND ITEM'\n\nYou can also reply with your Report ID to get status."
            elif "missing" in message_body:
                response = "To report a missing person, please visit our portal or reply with details in this format:\nName, Age, Gender, Description, Last seen location, Your WhatsApp number"
            elif "found person" in message_body:
                response = "To report a found person, please share details in this format:\nName (if known), Approx Age, Gender, Description, Current location, Your WhatsApp number"
            elif "lost" in message_body and "item" not in message_body:
                response = "To report a lost item, please reply:\nCategory, Color, Brand/Model, Description, Last seen location, Your WhatsApp number"
            elif "found item" in message_body:
                response = "To report a found item, please reply:\nCategory, Color, Brand/Model, Description, Found location, Your WhatsApp number"
            elif re.match(r'^[a-f0-9]{24}$', message_body):
                response = f"Looking up status for Report ID: {message_body}.\nPlease check the portal; if there's a match, we'll notify you here."
            else:
                response = "Sorry, I didn't understand. Please say 'Namaste' to see options or share your Report ID."
            
            return response
        except Exception as e:
            print(f"❌ Error processing incoming message: {e}")
            return "We encountered an error. Please try again later."

class WhatsAppNotifier:
    @staticmethod
    def send_match_notification(contact_number, match_type, match_details, similarity_score, report_id):
        try:
            if match_type == 'person':
                message = "✅ Potential MATCH found for your missing person report!\n\n"
                message += f"Name: {match_details.get('name','N/A')} (approx age {match_details.get('age','N/A')})\n"
                message += f"Gender: {match_details.get('gender','N/A')}\n"
                message += f"Description: {match_details.get('description','N/A')}\n"
                message += f"Location: {match_details.get('location','N/A')}\n"
                message += f"Found report contact: {match_details.get('contact','N/A')}\n\n"
                message += f"Similarity Score: {similarity_score}%\n\n"
                message += f"Please contact our help center for verification: +91-XXXXXXXXXX\n"
                message += f"Reference ID: {report_id}"
            else:
                message = "✅ Potential MATCH found for your lost item report!\n\n"
                message += f"Category: {match_details.get('category','N/A')} | Color: {match_details.get('color','N/A')} | Brand: {match_details.get('brand','N/A')}\n"
                message += f"Description: {match_details.get('description','N/A')}\n"
                message += f"Location: {match_details.get('location','N/A')}\n"
                message += f"Found by contact: {match_details.get('contact','N/A')}\n\n"
                message += f"Similarity Score: {similarity_score}%\n\n"
                message += f"Please contact our help center for verification: +91-XXXXXXXXXX\n"
                message += f"Reference ID: {report_id}"
            
            print(f"💬 Message content: {message[:100]}.")
            
            notification_id = notifications.insert_one({
                'type': 'match_alert',
                'contact_number': contact_number,
                'message': message,
                'status': 'pending',
                'sent_at': datetime.now(),
                'match_type': match_type,
                'similarity_score': similarity_score,
                'report_id': report_id
            }).inserted_id
            
            print(f"📝 Notification logged in DB with ID: {notification_id}")
            
            success = WhatsAppHandler.send_whatsapp_message(contact_number, message)
            
            if success:
                notifications.update_one(
                    {'_id': notification_id},
                    {'$set': {'status': 'sent'}}
                )
                print(f"✅ Notification sent successfully to {contact_number}")
            else:
                notifications.update_one(
                    {'_id': notification_id},
                    {'$set': {'status': 'failed'}}
                )
                print(f"❌ Failed to send notification to {contact_number}")
            
            return success
        except Exception as e:
            print(f"🔥 Error sending WhatsApp notification: {e}")
            return False

# Background notification worker
def notification_worker():
    """Background process to handle notifications"""
    while True:
        try:
            notification_data = notification_queue.get()
            if notification_data is None:
                break
            WhatsAppNotifier.send_match_notification(
                notification_data['contact_number'],
                notification_data['match_type'],
                notification_data['match_details'],
                notification_data['similarity_score'],
                notification_data.get('report_id', 'N/A')
            )
            notification_queue.task_done()
        except Exception as e:
            print(f"Error in notification worker: {e}")
            time.sleep(5)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Routes
@app.route('/')
def home():
    try:
        with open('index.html', 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        return "Simhastha Milaap - Smart Lost & Found System"

@app.route('/submit_person', methods=['POST'])
def submit_person():
    try:
        data = request.get_json()
        required_fields = ['report_type', 'name', 'age', 'gender', 'location', 'contact']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 4
        data['status'] = 'active'
        data['created_at'] = datetime.now()
        data['has_photos'] = False
        
        result = person_reports.insert_one(data)
        report_id = str(result.inserted_id)
        
        existing_reports = list(person_reports.find({'_id': {'$ne': result.inserted_id}, 'status': 'active'}))
        for report in existing_reports:
            report['_id'] = str(report['_id'])
        
        matches = SmartMatcher.match_persons(data, existing_reports)
        
        response = {
            'message': 'Person report submitted successfully',
            'report_id': report_id,
            'matches_found': len(matches)
        }
        
        if matches:
            response['potential_matches'] = []
            for match, score in matches:
                match_data = {
                    'match_details': match,
                    'similarity_score': round(score * 100, 2),
                    'match_message': f'Potential match found with {score*100:.1f}% confidence'
                }
                response['potential_matches'].append(match_data)
                
                notification_queue.put({
                    'contact_number': data['contact'],
                    'match_type': 'person',
                    'match_details': match,
                    'similarity_score': round(score * 100, 2),
                    'report_id': report_id
                })
                
                notification_queue.put({
                    'contact_number': match['contact'],
                    'match_type': 'person',
                    'match_details': data,
                    'similarity_score': round(score * 100, 2),
                    'report_id': str(match['_id'])
                })
        
        return jsonify(response), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/submit_item', methods=['POST'])
def submit_item():
    try:
        data = request.get_json()
        required_fields = ['report_type', 'category', 'color', 'location', 'contact']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        data['status'] = 'active'
        data['created_at'] = datetime.now()
        data['has_photos'] = False
        
        result = item_reports.insert_one(data)
        report_id = str(result.inserted_id)
        
        existing_reports = list(item_reports.find({'_id': {'$ne': result.inserted_id}, 'status': 'active'}))
        for report in existing_reports:
            report['_id'] = str(report['_id'])
        
        matches = SmartMatcher.match_items(data, existing_reports)
        
        response = {
            'message': 'Item report submitted successfully',
            'report_id': report_id,
            'matches_found': len(matches)
        }
        
        if matches:
            response['potential_matches'] = []
            for match, score in matches:
                match_data = {
                    'match_details': match,
                    'similarity_score': round(score * 100, 2),
                    'match_message': f'Potential match found with {score*100:.1f}% confidence'
                }
                response['potential_matches'].append(match_data)
                
                notification_queue.put({
                    'contact_number': data['contact'],
                    'match_type': 'item',
                    'match_details': match,
                    'similarity_score': round(score * 100, 2),
                    'report_id': report_id
                })
                
                notification_queue.put({
                    'contact_number': match['contact'],
                    'match_type': 'item',
                    'match_details': data,
                    'similarity_score': round(score * 100, 2),
                    'report_id': str(match['_id'])
                })
        
        return jsonify(response), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/get_persons', methods=['GET'])
def get_persons():
    try:
        report_type = request.args.get('type', 'all')
        status_filter = request.args.get('status', 'active')
        query = {'status': status_filter}
        if report_type != 'all':
            query['report_type'] = report_type
        persons = list(person_reports.find(query).sort('created_at', -1).limit(50))
        for person in persons:
            person['_id'] = str(person['_id'])
        return jsonify(persons), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/get_items', methods=['GET'])
def get_items():
    try:
        report_type = request.args.get('type', 'all')
        status_filter = request.args.get('status', 'active')
        query = {'status': status_filter}
        if report_type != 'all':
            query['report_type'] = report_type
        items = list(item_reports.find(query).sort('created_at', -1).limit(50))
        for item in items:
            item['_id'] = str(item['_id'])
        return jsonify(items), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/my-reports', methods=['GET'])
def my_reports():
    try:
        contact = request.args.get('contact')
        if not contact:
            return jsonify({'error': 'Missing contact parameter'}), 400
        
        person_query = {'contact': contact, 'status': 'active'}
        persons = list(person_reports.find(person_query).sort('created_at', -1))
        for p in persons:
            p['_id'] = str(p['_id'])
            p['type'] = 'person'
            if p['report_type'] == 'missing':
                existing = list(person_reports.find({'report_type': 'found', 'status': 'active'}))
                for e in existing:
                    e['_id'] = str(e['_id'])
                p['matches'] = SmartMatcher.match_persons(p, existing)
            else:
                p['matches'] = []
        
        item_query = {'contact': contact, 'status': 'active'}
        items = list(item_reports.find(item_query).sort('created_at', -1))
        for i in items:
            i['_id'] = str(i['_id'])
            i['type'] = 'item'
            if i['report_type'] == 'lost':
                existing = list(item_reports.find({'report_type': 'found', 'status': 'active'}))
                for e in existing:
                    e['_id'] = str(e['_id'])
                i['matches'] = SmartMatcher.match_items(i, existing)
            else:
                i['matches'] = []
        
        reports = persons + items
        return jsonify(reports), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    try:
        if request.is_json:
            data = request.get_json()
            from_number = data.get('from', '')
            message_body = data.get('body', '')
        else:
            from_number = request.form.get('From', '')
            message_body = request.form.get('Body', '')
        response_text = WhatsAppHandler.process_incoming_message(from_number, message_body)
        return jsonify({'response': response_text}), 200
    except Exception as e:
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    try:
        person_reports.find_one()
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/help-center')
def help_center_portal():
    """Serve volunteer portal (your file is named help_centre.html)"""
    try:
        with open('help_centre.html', 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        return "Help Center Portal - Page not found"

@app.route('/stats')
def get_statistics():
    try:
        missing_persons_count = person_reports.count_documents({'report_type': 'missing'})
        missing_persons_active = person_reports.count_documents({'report_type': 'missing', 'status': 'active'})
        found_persons_count = person_reports.count_documents({'report_type': 'found'})
        found_persons_active = person_reports.count_documents({'report_type': 'found', 'status': 'active'})
        lost_items_count = item_reports.count_documents({'report_type': 'lost'})
        lost_items_active = item_reports.count_documents({'report_type': 'lost', 'status': 'active'})
        found_items_count = item_reports.count_documents({'report_type': 'found'})
        found_items_active = item_reports.count_documents({'report_type': 'found', 'status': 'active'})
        notifications_sent = notifications.count_documents({'type': 'match_alert', 'status': 'sent'})
        notifications_failed = notifications.count_documents({'type': 'match_alert', 'status': 'failed'})
        
        stats = {
            'persons': {
                'missing': {'total_count': missing_persons_count, 'active_count': missing_persons_active},
                'found': {'total_count': found_persons_count, 'active_count': found_persons_active}
            },
            'items': {
                'lost': {'total_count': lost_items_count, 'active_count': lost_items_active},
                'found': {'total_count': found_items_count, 'active_count': found_items_active}
            },
            'notifications': {'sent': notifications_sent, 'failed': notifications_failed}
        }
        return jsonify(stats), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/resolve-report', methods=['POST'])
def resolve_report():
    try:
        data = request.get_json()
        required_fields = ['report_id', 'report_type']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        if data['report_type'] == 'person':
            result = person_reports.update_one(
                {'_id': ObjectId(data['report_id'])},
                {'$set': {'status': 'resolved', 'resolved_at': datetime.now()}}
            )
        else:
            result = item_reports.update_one(
                {'_id': ObjectId(data['report_id'])},
                {'$set': {'status': 'resolved', 'resolved_at': datetime.now()}}
            )
        
        if result.modified_count > 0:
            return jsonify({'message': 'Report resolved successfully'}), 200
        else:
            return jsonify({'error': 'Report not found or already resolved'}), 404
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/notify-match', methods=['POST'])
def notify_manual_match():
    try:
        data = request.get_json()
        required_fields = ['contact_number', 'match_type', 'match_details']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        similarity_score = data.get('similarity_score', 80)
        report_id = data.get('report_id', 'N/A')
        
        notification_queue.put({
            'contact_number': data['contact_number'],
            'match_type': data['match_type'],
            'match_details': data['match_details'],
            'similarity_score': similarity_score,
            'report_id': report_id
        })
        
        return jsonify({'message': 'Notification queued successfully'}), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/get-notifications', methods=['GET'])
def get_notifications():
    try:
        limit = int(request.args.get('limit', 20))
        recent_notifications = list(notifications.find().sort('sent_at', -1).limit(limit))
        
        for notification in recent_notifications:
            notification['_id'] = str(notification['_id'])
            if 'sent_at' in notification:
                notification['sent_at'] = notification['sent_at'].isoformat()
            if 'received_at' in notification:
                notification['received_at'] = notification['received_at'].isoformat()
        
        return jsonify(recent_notifications), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/init-db')
def initialize_database():
    try:
        person_reports.update_many({'status': {'$exists': False}}, {'$set': {'status': 'active'}})
        item_reports.update_many({'status': {'$exists': False}}, {'$set': {'status': 'active'}})
        if 'notifications' not in db.list_collection_names():
            db.create_collection('notifications')
        return jsonify({'message': 'Database initialized successfully'}), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/upload-photo', methods=['POST'])
def upload_photo():
    try:
        report_id = request.form.get('report_id')
        report_type = request.form.get('report_type')
        
        if 'photo' not in request.files:
            return jsonify({'error': 'No photo provided'}), 400
        
        file = request.files['photo']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
        
        if file and allowed_file(file.filename):
            filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            photo_data = {
                'report_id': report_id,
                'report_type': report_type,
                'filename': filename,
                'filepath': filepath,
                'uploaded_at': datetime.now(),
                'uploaded_by': 'user'
            }
            
            photos.insert_one(photo_data)
            
            if report_type == 'person':
                person_reports.update_one(
                    {'_id': ObjectId(report_id)},
                    {'$set': {'has_photos': True}}
                )
            else:
                item_reports.update_one(
                    {'_id': ObjectId(report_id)},
                    {'$set': {'has_photos': True}}
                )
            
            return jsonify({'message': 'Photo uploaded successfully', 'filename': filename}), 200
        else:
            return jsonify({'error': 'Invalid file type'}), 400
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/get-photos/<report_id>')
def get_photos(report_id):
    try:
        photo_list = list(photos.find({'report_id': report_id}))
        for photo in photo_list:
            photo['_id'] = str(photo['_id'])
            with open(photo['filepath'], 'rb') as img_file:
                photo['image_data'] = base64.b64encode(img_file.read()).decode('utf-8')
            del photo['filepath']
        return jsonify(photo_list), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/reports-with-photos')
def get_reports_with_photos():
    try:
        person_reports_with_photos = list(person_reports.find({'has_photos': True}))
        for report in person_reports_with_photos:
            report['_id'] = str(report['_id'])
            report['type'] = 'person'
        
        item_reports_with_photos = list(item_reports.find({'has_photos': True}))
        for report in item_reports_with_photos:
            report['_id'] = str(report['_id'])
            report['type'] = 'item'
        
        all_reports = person_reports_with_photos + item_reports_with_photos
        return jsonify(all_reports), 200
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500
if __name__ == '__main__':
    notification_thread = threading.Thread(target=notification_worker, daemon=True)
    notification_thread.start()
    print("✅ Notification worker thread started")

    print("\n🌐 Open the portals in your browser:")
    print("   🟢 User Portal:   http://127.0.0.1:5000/")
    print("   🟢 Help Center:   http://127.0.0.1:5000/help-center\n")

    app.run(debug=True, host='0.0.0.0', port=5000)

