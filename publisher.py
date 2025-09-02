#!/usr/bin/env python3
"""
Smart Home Sensor Data Publisher
Simulates temperature and humidity sensors and publishes data to MQTT broker
"""
import json
import time
import random
import os
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import paho.mqtt.client as mqtt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Thread-safe HTTP Server"""
    daemon_threads = True

class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP request handler for health checks"""
    
    def __init__(self, sensor_simulator, *args, **kwargs):
        self.sensor_simulator = sensor_simulator
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/health':
            self.handle_health_check()
        elif self.path == '/status':
            self.handle_status_check()
        elif self.path == '/metrics':
            self.handle_metrics()
        else:
            self.send_error(404, 'Not Found')
    
    def handle_health_check(self):
        """Handle basic health check endpoint"""
        try:
            # Check if MQTT client is connected
            if self.sensor_simulator.client.is_connected():
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'OK')
            else:
                self.send_response(503)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'MQTT_DISCONNECTED')
        except Exception as e:
            logger.error(f"Health check error: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(f'ERROR: {str(e)}'.encode())
    
    def handle_status_check(self):
        """Handle detailed status endpoint"""
        try:
            status = {
                'service': 'smart-home-publisher',
                'version': '1.0.0',
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'mqtt_connected': self.sensor_simulator.client.is_connected(),
                'mqtt_broker': f"{self.sensor_simulator.mqtt_broker}:{self.sensor_simulator.mqtt_port}",
                'sensors_count': len(self.sensor_simulator.sensors),
                'last_publish': self.sensor_simulator.last_publish_time,
                'publish_count': self.sensor_simulator.publish_count,
                'error_count': self.sensor_simulator.error_count
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(status, indent=2).encode())
            
        except Exception as e:
            logger.error(f"Status check error: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            error_response = {'error': str(e)}
            self.wfile.write(json.dumps(error_response).encode())
    
    def handle_metrics(self):
        """Handle metrics endpoint (Prometheus format)"""
        try:
            metrics = f"""# HELP smarthome_publisher_mqtt_connected MQTT connection status
# TYPE smarthome_publisher_mqtt_connected gauge
smarthome_publisher_mqtt_connected {1 if self.sensor_simulator.client.is_connected() else 0}

# HELP smarthome_publisher_publish_total Total number of publishes
# TYPE smarthome_publisher_publish_total counter
smarthome_publisher_publish_total {self.sensor_simulator.publish_count}

# HELP smarthome_publisher_errors_total Total number of errors
# TYPE smarthome_publisher_errors_total counter
smarthome_publisher_errors_total {self.sensor_simulator.error_count}

# HELP smarthome_publisher_sensors_count Number of configured sensors
# TYPE smarthome_publisher_sensors_count gauge
smarthome_publisher_sensors_count {len(self.sensor_simulator.sensors)}
"""
            
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; version=0.0.4; charset=utf-8')
            self.end_headers()
            self.wfile.write(metrics.encode())
            
        except Exception as e:
            logger.error(f"Metrics error: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(f'ERROR: {str(e)}'.encode())
    
    def log_message(self, format, *args):
        """Override to reduce HTTP server logging noise"""
        # Only log errors and important requests
        if '500' in format or '404' in format:
            logger.warning(f"HTTP: {format % args}")

class SensorSimulator:
    def __init__(self, mqtt_broker, mqtt_port=1883, health_port=8080):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.health_port = health_port
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_publish = self.on_publish
        
        # Health check server
        self.health_server = None
        self.health_thread = None
        
        # Statistics for monitoring
        self.publish_count = 0
        self.error_count = 0
        self.last_publish_time = None
        self.running = False
        
        # Sensor configurations
        self.sensors = {
            'living_room': {
                'temp_base': 22.0,
                'temp_variation': 3.0,
                'humidity_base': 45.0,
                'humidity_variation': 10.0
            },
            'bedroom': {
                'temp_base': 20.0,
                'temp_variation': 2.5,
                'humidity_base': 50.0,
                'humidity_variation': 8.0
            },
            'kitchen': {
                'temp_base': 24.0,
                'temp_variation': 4.0,
                'humidity_base': 55.0,
                'humidity_variation': 12.0
            },
            'bathroom': {
                'temp_base': 23.0,
                'temp_variation': 2.0,
                'humidity_base': 65.0,
                'humidity_variation': 15.0
            }
        }
        
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")
            self.error_count += 1
    
    def on_disconnect(self, client, userdata, rc):
        logger.info("Disconnected from MQTT broker")
        if rc != 0:
            self.error_count += 1
    
    def on_publish(self, client, userdata, mid):
        """Callback for successful publish"""
        self.publish_count += 1
        self.last_publish_time = datetime.utcnow().isoformat() + 'Z'
    
    def start_health_server(self):
        """Start the health check HTTP server"""
        try:
            # Create a handler class that has access to the simulator instance
            def handler_factory(*args, **kwargs):
                return HealthCheckHandler(self, *args, **kwargs)
            
            self.health_server = ThreadedHTTPServer(('0.0.0.0', self.health_port), handler_factory)
            self.health_thread = threading.Thread(target=self.health_server.serve_forever)
            self.health_thread.daemon = True
            self.health_thread.start()
            
            logger.info(f"Health check server started on port {self.health_port}")
            logger.info(f"Health endpoints:")
            logger.info(f"  - http://localhost:{self.health_port}/health (basic health check)")
            logger.info(f"  - http://localhost:{self.health_port}/status (detailed status)")
            logger.info(f"  - http://localhost:{self.health_port}/metrics (Prometheus metrics)")
            
        except Exception as e:
            logger.error(f"Failed to start health server: {e}")
            self.error_count += 1
    
    def stop_health_server(self):
        """Stop the health check HTTP server"""
        if self.health_server:
            self.health_server.shutdown()
            self.health_server.server_close()
            logger.info("Health check server stopped")
    
    def connect(self):
        """Connect to MQTT broker with retry logic"""
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                logger.info(f"Attempting to connect to MQTT broker... (attempt {retry_count + 1}/{max_retries})")
                self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
                self.client.loop_start()
                
                # Wait for connection to establish
                connection_timeout = 10
                start_time = time.time()
                while not self.client.is_connected() and (time.time() - start_time) < connection_timeout:
                    time.sleep(0.1)
                
                if self.client.is_connected():
                    return True
                else:
                    raise Exception("Connection timeout")
                    
            except Exception as e:
                logger.error(f"Connection attempt {retry_count + 1} failed: {e}")
                self.error_count += 1
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = min(2 ** retry_count, 30)  # Exponential backoff, max 30 seconds
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
        
        logger.error(f"Failed to connect after {max_retries} attempts")
        return False
    
    def simulate_temperature(self, base_temp, variation):
        """Simulate realistic temperature readings with gradual changes"""
        # Add some daily variation (assuming time of day affects temperature)
        hour = datetime.now().hour
        daily_factor = 1 + 0.1 * (hour - 12) / 12  # Peak at noon, lowest at midnight
        
        # Add random variation
        noise = random.uniform(-variation/2, variation/2)
        temp = base_temp * daily_factor + noise
        
        return round(temp, 2)
    
    def simulate_humidity(self, base_humidity, variation):
        """Simulate realistic humidity readings"""
        # Humidity often inversely correlates with temperature
        noise = random.uniform(-variation/2, variation/2)
        humidity = base_humidity + noise
        
        # Keep humidity within realistic bounds
        humidity = max(20.0, min(80.0, humidity))
        
        return round(humidity, 2)
    
    def generate_sensor_data(self, room):
        """Generate sensor data for a specific room"""
        config = self.sensors[room]
        
        temperature = self.simulate_temperature(
            config['temp_base'], 
            config['temp_variation']
        )
        
        humidity = self.simulate_humidity(
            config['humidity_base'], 
            config['humidity_variation']
        )
        
        # Create sensor data payload
        sensor_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'room': room,
            'temperature': temperature,
            'humidity': humidity,
            'unit_temp': 'celsius',
            'unit_humidity': 'percent',
            'device_id': f"sensor_{room}",
            'battery_level': random.randint(70, 100)  # Simulate battery level
        }
        
        return sensor_data
    
    def publish_sensor_data(self):
        """Publish sensor data for all rooms"""
        if not self.client.is_connected():
            logger.warning("MQTT client not connected. Attempting to reconnect...")
            if not self.connect():
                logger.error("Failed to reconnect to MQTT broker")
                return
        
        for room in self.sensors.keys():
            try:
                sensor_data = self.generate_sensor_data(room)
                topic = f"smarthome/sensors/{room}"
                
                # Publish the data
                result = self.client.publish(
                    topic, 
                    json.dumps(sensor_data), 
                    qos=1,
                    retain=False
                )
                
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.info(f"Published data for {room}: "
                              f"Temp={sensor_data['temperature']}°C, "
                              f"Humidity={sensor_data['humidity']}%")
                else:
                    logger.error(f"Failed to publish data for {room}, return code: {result.rc}")
                    self.error_count += 1
                
            except Exception as e:
                logger.error(f"Error publishing data for {room}: {e}")
                self.error_count += 1
    
    def run(self):
        """Main loop to continuously publish sensor data"""
        # Start health check server
        self.start_health_server()
        
        if not self.connect():
            logger.error("Failed to connect to MQTT broker. Exiting.")
            return
        
        self.running = True
        logger.info("Starting sensor data simulation...")
        logger.info(f"Publishing interval: 30 seconds")
        logger.info(f"Configured sensors: {list(self.sensors.keys())}")
        
        try:
            while self.running:
                self.publish_sensor_data()
                time.sleep(30)  # Publish every 30 seconds
                
        except KeyboardInterrupt:
            logger.info("Stopping sensor simulation...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            self.error_count += 1
        finally:
            self.running = False
            self.client.loop_stop()
            self.client.disconnect()
            self.stop_health_server()
            logger.info("Sensor simulator stopped")

def main():
    # Get configuration from environment variables
    mqtt_broker = os.getenv('MQTT_BROKER', 'localhost')
    mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
    health_port = int(os.getenv('HEALTH_PORT', '8080'))
    
    logger.info("=== Smart Home Sensor Publisher ===")
    logger.info(f"MQTT Broker: {mqtt_broker}:{mqtt_port}")
    logger.info(f"Health Check Port: {health_port}")
    
    # Create and run sensor simulator
    simulator = SensorSimulator(mqtt_broker, mqtt_port, health_port)
    simulator.run()

if __name__ == "__main__":
    main()

