#!/usr/bin/env python3
"""
Smart Home Data Subscriber
Subscribes to MQTT sensor data and stores it in InfluxDB
"""
import json
import os
import logging
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

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
    
    def __init__(self, subscriber, *args, **kwargs):
        self.subscriber = subscriber
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
            # Check if both MQTT and InfluxDB connections are healthy
            mqtt_ok = self.subscriber.mqtt_client.is_connected()
            influx_ok = self.subscriber.influx_client is not None
            
            if mqtt_ok and influx_ok:
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'OK')
            else:
                self.send_response(503)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                if not mqtt_ok and not influx_ok:
                    self.wfile.write(b'MQTT_AND_INFLUXDB_DISCONNECTED')
                elif not mqtt_ok:
                    self.wfile.write(b'MQTT_DISCONNECTED')
                else:
                    self.wfile.write(b'INFLUXDB_DISCONNECTED')
        except Exception as e:
            logger.error(f"Health check error: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(f'ERROR: {str(e)}'.encode())
    
    def handle_status_check(self):
        """Handle detailed status endpoint"""
        try:
            # Test InfluxDB connection
            influxdb_healthy = False
            if self.subscriber.influx_client:
                try:
                    health = self.subscriber.influx_client.health()
                    influxdb_healthy = health.status == "pass"
                except:
                    influxdb_healthy = False
            
            status = {
                'service': 'smart-home-subscriber',
                'version': '1.0.0',
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'mqtt': {
                    'connected': self.subscriber.mqtt_client.is_connected(),
                    'broker': f"{self.subscriber.mqtt_broker}:{self.subscriber.mqtt_port}",
                    'subscribed_topics': ['smarthome/sensors/+']
                },
                'influxdb': {
                    'connected': influxdb_healthy,
                    'url': self.subscriber.influxdb_url,
                    'org': self.subscriber.influxdb_org,
                    'bucket': self.subscriber.influxdb_bucket
                },
                'statistics': {
                    'messages_received': self.subscriber.message_count,
                    'messages_written': self.subscriber.write_count,
                    'errors': self.subscriber.error_count,
                    'last_message_time': self.subscriber.last_message_time,
                    'last_write_time': self.subscriber.last_write_time
                }
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
            metrics = f"""# HELP smarthome_subscriber_mqtt_connected MQTT connection status
# TYPE smarthome_subscriber_mqtt_connected gauge
smarthome_subscriber_mqtt_connected {1 if self.subscriber.mqtt_client.is_connected() else 0}

# HELP smarthome_subscriber_influxdb_connected InfluxDB connection status
# TYPE smarthome_subscriber_influxdb_connected gauge
smarthome_subscriber_influxdb_connected {1 if self.subscriber.influx_client else 0}

# HELP smarthome_subscriber_messages_received_total Total MQTT messages received
# TYPE smarthome_subscriber_messages_received_total counter
smarthome_subscriber_messages_received_total {self.subscriber.message_count}

# HELP smarthome_subscriber_messages_written_total Total messages written to InfluxDB
# TYPE smarthome_subscriber_messages_written_total counter
smarthome_subscriber_messages_written_total {self.subscriber.write_count}

# HELP smarthome_subscriber_errors_total Total number of errors
# TYPE smarthome_subscriber_errors_total counter
smarthome_subscriber_errors_total {self.subscriber.error_count}
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

class SensorDataSubscriber:
    def __init__(self, mqtt_broker, mqtt_port, influxdb_url, influxdb_token, 
                 influxdb_org, influxdb_bucket, health_port=8080):
        # MQTT Configuration
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        
        # InfluxDB Configuration
        self.influxdb_url = influxdb_url
        self.influxdb_token = influxdb_token
        self.influxdb_org = influxdb_org
        self.influxdb_bucket = influxdb_bucket
        
        # Initialize InfluxDB client
        self.influx_client = None
        self.write_api = None
        
        # Health check server
        self.health_port = health_port
        self.health_server = None
        self.health_thread = None
        
        # Statistics for monitoring
        self.message_count = 0
        self.write_count = 0
        self.error_count = 0
        self.last_message_time = None
        self.last_write_time = None
        self.running = False
        
        # Thread lock for statistics
        self._stats_lock = threading.Lock()
    
    def start_health_server(self):
        """Start the health check HTTP server"""
        try:
            # Create a handler class that has access to the subscriber instance
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
            with self._stats_lock:
                self.error_count += 1
    
    def stop_health_server(self):
        """Stop the health check HTTP server"""
        if self.health_server:
            self.health_server.shutdown()
            self.health_server.server_close()
            logger.info("Health check server stopped")
    
    def connect_influxdb(self):
        """Connect to InfluxDB with retry logic"""
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                logger.info(f"Attempting to connect to InfluxDB... (attempt {retry_count + 1}/{max_retries})")
                
                self.influx_client = InfluxDBClient(
                    url=self.influxdb_url,
                    token=self.influxdb_token,
                    org=self.influxdb_org
                )
                
                # Test connection
                health = self.influx_client.health()
                if health.status == "pass":
                    logger.info("Connected to InfluxDB successfully")
                    self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
                    return True
                else:
                    raise Exception(f"InfluxDB health check failed: {health.message}")
                    
            except Exception as e:
                logger.error(f"InfluxDB connection attempt {retry_count + 1} failed: {e}")
                with self._stats_lock:
                    self.error_count += 1
                
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = min(2 ** retry_count, 30)  # Exponential backoff, max 30 seconds
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
        
        logger.error(f"Failed to connect to InfluxDB after {max_retries} attempts")
        return False
    
    def on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback for MQTT connection"""
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            # Subscribe to all sensor topics
            client.subscribe("smarthome/sensors/+")
            logger.info("Subscribed to smarthome/sensors/+ topics")
        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")
            with self._stats_lock:
                self.error_count += 1
    
    def on_mqtt_disconnect(self, client, userdata, rc):
        """Callback for MQTT disconnection"""
        logger.info("Disconnected from MQTT broker")
        if rc != 0:
            with self._stats_lock:
                self.error_count += 1
    
    def on_mqtt_message(self, client, userdata, msg):
        """Callback for MQTT message received"""
        try:
            # Update statistics
            with self._stats_lock:
                self.message_count += 1
                self.last_message_time = datetime.utcnow().isoformat() + 'Z'
            
            # Decode the message
            topic = msg.topic
            payload = json.loads(msg.payload.decode())
            
            logger.info(f"Received data from {topic}: {payload}")
            
            # Write to InfluxDB
            self.write_to_influxdb(payload)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
            with self._stats_lock:
                self.error_count += 1
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
            with self._stats_lock:
                self.error_count += 1
    
    def write_to_influxdb(self, sensor_data):
        """Write sensor data to InfluxDB with retry logic"""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Check if we have a valid write API
                if not self.write_api:
                    logger.warning("InfluxDB write API not available. Attempting to reconnect...")
                    if not self.connect_influxdb():
                        raise Exception("Failed to reconnect to InfluxDB")
                
                # Create InfluxDB point with additional fields if available
                point = Point("sensor_data") \
                    .tag("room", sensor_data['room']) \
                    .field("temperature", float(sensor_data['temperature'])) \
                    .field("humidity", float(sensor_data['humidity'])) \
                    .time(sensor_data['timestamp'])
                
                # Add optional fields if present
                if 'device_id' in sensor_data:
                    point = point.tag("device_id", sensor_data['device_id'])
                
                if 'battery_level' in sensor_data:
                    point = point.field("battery_level", int(sensor_data['battery_level']))
                
                # Write to InfluxDB
                self.write_api.write(
                    bucket=self.influxdb_bucket,
                    org=self.influxdb_org,
                    record=point
                )
                
                # Update statistics
                with self._stats_lock:
                    self.write_count += 1
                    self.last_write_time = datetime.utcnow().isoformat() + 'Z'
                
                logger.info(f"Written to InfluxDB: {sensor_data['room']} - "
                           f"Temp: {sensor_data['temperature']}°C, "
                           f"Humidity: {sensor_data['humidity']}%")
                
                return  # Success, exit retry loop
                
            except Exception as e:
                logger.error(f"Failed to write to InfluxDB (attempt {retry_count + 1}/{max_retries}): {e}")
                with self._stats_lock:
                    self.error_count += 1
                
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 2 ** retry_count  # Exponential backoff
                    logger.info(f"Retrying write in {wait_time} seconds...")
                    time.sleep(wait_time)
                    
                    # Try to reconnect on failure
                    if retry_count == 2:  # On second failure, try to reconnect
                        logger.info("Attempting to reconnect to InfluxDB...")
                        self.connect_influxdb()
        
        logger.error(f"Failed to write to InfluxDB after {max_retries} attempts")
    
    def connect_mqtt(self):
        """Connect to MQTT broker with retry logic"""
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                logger.info(f"Attempting to connect to MQTT broker... (attempt {retry_count + 1}/{max_retries})")
                self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
                
                # Wait for connection to establish
                connection_timeout = 10
                start_time = time.time()
                while not self.mqtt_client.is_connected() and (time.time() - start_time) < connection_timeout:
                    time.sleep(0.1)
                
                if self.mqtt_client.is_connected():
                    return True
                else:
                    raise Exception("Connection timeout")
                    
            except Exception as e:
                logger.error(f"MQTT connection attempt {retry_count + 1} failed: {e}")
                with self._stats_lock:
                    self.error_count += 1
                
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = min(2 ** retry_count, 30)  # Exponential backoff, max 30 seconds
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
        
        logger.error(f"Failed to connect to MQTT broker after {max_retries} attempts")
        return False
    
    def run(self):
        """Main loop to run the subscriber"""
        self.running = True
        
        # Start health check server
        self.start_health_server()
        
        # Connect to InfluxDB
        if not self.connect_influxdb():
            logger.error("Failed to connect to InfluxDB. Exiting.")
            return
        
        # Connect to MQTT
        if not self.connect_mqtt():
            logger.error("Failed to connect to MQTT broker. Exiting.")
            return
        
        logger.info("Starting data subscriber...")
        logger.info(f"Subscribed to: smarthome/sensors/+")
        logger.info(f"Writing to InfluxDB: {self.influxdb_url}")
        logger.info(f"Organization: {self.influxdb_org}, Bucket: {self.influxdb_bucket}")
        
        try:
            # Start MQTT loop
            self.mqtt_client.loop_forever()
            
        except KeyboardInterrupt:
            logger.info("Stopping data subscriber...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            with self._stats_lock:
                self.error_count += 1
        finally:
            self.running = False
            self.mqtt_client.disconnect()
            if self.influx_client:
                self.influx_client.close()
            self.stop_health_server()
            logger.info("Data subscriber stopped")

def main():
    # Get configuration from environment variables
    mqtt_broker = os.getenv('MQTT_BROKER', 'localhost')
    mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
    influxdb_url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
    influxdb_token = os.getenv('INFLUXDB_TOKEN', 'my-super-secret-auth-token')
    influxdb_org = os.getenv('INFLUXDB_ORG', 'smarthome')
    influxdb_bucket = os.getenv('INFLUXDB_BUCKET', 'sensor_data')
    health_port = int(os.getenv('HEALTH_PORT', '8080'))
    
    logger.info("=== Smart Home Data Subscriber ===")
    logger.info(f"MQTT Broker: {mqtt_broker}:{mqtt_port}")
    logger.info(f"InfluxDB: {influxdb_url}")
    logger.info(f"Organization: {influxdb_org}, Bucket: {influxdb_bucket}")
    logger.info(f"Health Check Port: {health_port}")
    
    # Create and run subscriber
    subscriber = SensorDataSubscriber(
        mqtt_broker=mqtt_broker,
        mqtt_port=mqtt_port,
        influxdb_url=influxdb_url,
        influxdb_token=influxdb_token,
        influxdb_org=influxdb_org,
        influxdb_bucket=influxdb_bucket,
        health_port=health_port
    )
    
    subscriber.run()

if __name__ == "__main__":
    main()

