from graphviz import Digraph

# Initialize the diagram
dot = Digraph('User_Flow', filename='user_flow_diagram', format='png')
dot.attr(rankdir='TD', size='10,10', fontname='Helvetica')

# Custom node styles
dot.attr('node', shape='box', style='filled', fillcolor='#0984e3', fontcolor='white', fontname='Helvetica')
dot.node('Start', 'Start: User Arrives at App', shape='ellipse', fillcolor='#2d3436')

# --- PATH 1: Remote Access ---
dot.node('ScanQR', 'Scan QR Code')
dot.node('CheckQR', 'ID exists in users.csv?', shape='diamond', fillcolor='#fdcb6e', fontcolor='black')
dot.node('QRError', 'Error: Not Recognized', fillcolor='#d63031')
dot.node('LocationCheck', 'Request GPS Coordinates')
dot.node('CheckDistance', 'Distance <= 1000m?', shape='diamond', fillcolor='#fdcb6e', fontcolor='black')
dot.node('DistanceError', 'Access Denied: Too Far', fillcolor='#d63031')
dot.node('DashboardAccess', 'Success: Dashboard Access', shape='ellipse', fillcolor='#00b894')

# --- PATH 2: Physical Entry ---
dot.node('LivenessCheck', 'Liveness Detection')
dot.node('CheckBlinks', 'Required Blinks?', shape='diamond', fillcolor='#fdcb6e', fontcolor='black')
dot.node('LivenessError', 'Error: Liveness Failed', fillcolor='#d63031')
dot.node('FaceRec', 'LBPH Face Recognition')
dot.node('CheckFace', 'Match > 75%?', shape='diamond', fillcolor='#fdcb6e', fontcolor='black')
dot.node('FaceError', 'Error: User Not Recognized', fillcolor='#d63031')
dot.node('EntryMonitoring', 'Success: Entry Monitored', shape='ellipse', fillcolor='#00b894')
dot.node('TailgatingCheck', 'Person Count > 1?', shape='diamond', fillcolor='#fdcb6e', fontcolor='black')
dot.node('TailgatingAlert', 'Trigger Tailgating Event!', fillcolor='#d63031')
dot.node('SafeEntry', 'Secure Solo Entry', shape='ellipse', fillcolor='#00b894')

# --- PATH 3: Admin Flow ---
dot.node('AdminLogin', 'Admin Portal Login')
dot.node('CheckAdmin', 'Valid Credentials?', shape='diamond', fillcolor='#fdcb6e', fontcolor='black')
dot.node('AdminError', 'Login Failed', fillcolor='#d63031')
dot.node('AddUser', 'Enter New User ID & Name')
dot.node('CaptureFace', 'Capture 50 Face Frames')
dot.node('TrainModel', 'Execute recognizer.train')
dot.node('ModelReady', 'System Updated', shape='ellipse', fillcolor='#00b894')

# Define Edges
dot.edges([
    ('Start', 'ScanQR'), ('Start', 'LivenessCheck'), ('Start', 'AdminLogin'),
    
    # Path 1
    ('ScanQR', 'CheckQR'), ('CheckQR', 'LocationCheck'), 
    ('CheckQR', 'QRError'), ('LocationCheck', 'CheckDistance'),
    ('CheckDistance', 'DashboardAccess'), ('CheckDistance', 'DistanceError'),
    
    # Path 2
    ('LivenessCheck', 'CheckBlinks'), ('CheckBlinks', 'FaceRec'),
    ('CheckBlinks', 'LivenessError'), ('FaceRec', 'CheckFace'),
    ('CheckFace', 'EntryMonitoring'), ('CheckFace', 'FaceError'),
    ('EntryMonitoring', 'TailgatingCheck'), ('TailgatingCheck', 'TailgatingAlert'),
    ('TailgatingCheck', 'SafeEntry'),
    
    # Path 3
    ('AdminLogin', 'CheckAdmin'), ('CheckAdmin', 'AddUser'),
    ('CheckAdmin', 'AdminError'), ('AddUser', 'CaptureFace'),
    ('CaptureFace', 'TrainModel'), ('TrainModel', 'ModelReady')
])

# Render to PNG
try:
    dot.render(cleanup=True)
    print("Success! Your PNG is ready: user_flow_diagram.png")
except Exception as e:
    print(f"Make sure Graphviz is installed on your OS! Error: {e}")