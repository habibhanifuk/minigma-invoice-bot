# premium_manager.py
import datetime

class PremiumManager:
    def __init__(self, filename='premium_users.txt'):
        self.filename = filename
        self.premium_users = set()
        self.load_premium_users()
    
    def load_premium_users(self):
        """Load premium users from file"""
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):  # Skip comments and empty lines
                        parts = line.split('|')
                        if parts:  # First part is user ID
                            user_id = parts[0].strip()
                            if user_id.isdigit():
                                self.premium_users.add(int(user_id))
            print(f"Loaded {len(self.premium_users)} premium users")
        except FileNotFoundError:
            # Create file if it doesn't exist
            with open(self.filename, 'w', encoding='utf-8') as f:
                f.write("# Premium Users List\n# Format: TelegramUserID | Username (optional) | ActivatedDate\n")
            print("Created new premium users file")
    
    def is_premium(self, user_id):
        """Check if user has premium access"""
        return user_id in self.premium_users
    
    def add_premium_user(self, user_id, username=""):
        """Add a new premium user to the file"""
        if user_id in self.premium_users:
            return False, "User already has premium access"
        
        # Add to memory
        self.premium_users.add(user_id)
        
        # Add to file
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        with open(self.filename, 'a', encoding='utf-8') as f:
            if username:
                f.write(f"\n{user_id} | {username} | {today}")
            else:
                f.write(f"\n{user_id} | | {today}")
        
        return True, f"✅ User {user_id} added to premium users"
    
    def remove_premium_user(self, user_id):
        """Remove a user from premium access"""
        if user_id not in self.premium_users:
            return False, "User not in premium list"
        
        # Remove from memory
        self.premium_users.remove(user_id)
        
        # Rewrite file without the user
        with open(self.filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        with open(self.filename, 'w', encoding='utf-8') as f:
            for line in lines:
                if line.strip() and not line.strip().startswith('#'):
                    parts = line.split('|')
                    if parts and parts[0].strip() != str(user_id):
                        f.write(line)
                else:
                    f.write(line)
        
        return True, f"❌ User {user_id} removed from premium users"

# Create global instance
premium_manager = PremiumManager()