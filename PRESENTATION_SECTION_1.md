# Presentation Section 1: Architecture, Intelligence & Registry
**Presenter: Lead Developer (Section 1)**

## 1. System Overview & Architecture
Our system is a **Containerized Smart Warehouse IoT Platform**. It is built using a **Microservice Architecture** where each service is isolated in its own Docker container.

### Key Architecture Concepts
*   **Decoupling**: Services talk to each other through an **MQTT Broker**, meaning they don't need to know where other services are located.
*   **Hybrid Communication**: We use **MQTT** for real-time sensor data (high speed) and **REST APIs** for the Dashboard and configuration (user interaction).

## 2. The Intelligence: Smart Controller
**File**: [controller_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/controller-service/controller_service.py)
This is the "brain" of the system. 

*   **Line 115**: `on_message` - This is where the controller receives data from the MQTT bus.
*   **Line 135**: `evaluate_rules` - The controller passes the sensor data to the **Rule Engine**, which decides if the warehouse is safe or if an action (like turning on a fan) is needed.
*   **Line 255**: `publish_command` - Once a decision is made, the controller sends an actuation command back to the MQTT bus using **QoS 2** (Guaranteed Delivery).

## 3. The Registry: Catalog Service
**File**: [catalog_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/catalog-service/catalog_service.py)
The Catalog is the "Source of Truth" for the entire system.

### **Demo: How to add a new warehouse**
To add a new warehouse during the presentation:
1.  Open [catalog.json](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/catalog-service/catalog.json).
2.  Add a new entry to the `assets` list.
3.  Explain that the **Sensor Simulator** (L62) and **Dashboard** (L367) will automatically see this new entry without any code changes or restarts.

## 4. Key Takeaway
This architecture allows us to scale to thousands of warehouses by simply adding them to the Catalog. The Controller and Simulator handle the new data automatically.
