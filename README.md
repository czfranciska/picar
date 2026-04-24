# picar

# Software control platform for a remote-controlled car

This project features a control platform and a simple line-follower application. The system is designed to operate on a distributed architecture, where the control logic and the physical vehicle are decoupled, allowing for flexible deployment across different hardware configurations. The architecture consists of three main components:

1. **The Raspberry Pi (The car):** The physical vehicle is mounted with a microcomputer. It translates digital control commands into physical electrical signals for the motors and streams the live camera feed.
2. **The central computer (Server):** A separate computer that acts as the central coordinator of the communication pipeline, routing data between the car and the user.
3. **The operator's computer (Client):** The machine used for manual operation or autonomous line-following.

Technically all three components can be run on the same machine, but for the sake of demonstration and to simulate a real-world scenario, it is recommended to run them on separate devices.

## Installation
Before proceeding, ensure that Python 3.10 or a later version is installed on all participating nodes. Install the project and the dependencies listed in pyproject.toml on all the machines by running the following command:

```bash
pip install .
```

## Running the components

Once the installations are completed, start the components using the provided entry points defined in pyproject.toml by executing the following instructions.

On the car:

```bash
pi-server
```
On the server:

```bash
pc-server
```
## Application usage
Following the initialisation of the core components, the system supports two main applications: manual navigation via the control app or autonomous movement using the line-follower algorithm.

### Manual control application
Open the client.html web interface on the client machine. The car can be controlled by keyboard.

### Line-follower application
The applications can be run from the root directory of the project by running the command below on the client node:

```bash
python3 picar_core/linefollower_app/linefollower.py
```

This will start the line-follower application, which processes the live camera feed from the car to detect and follow a dark line on the ground. The algorithm uses computer vision techniques to identify the line and sends appropriate control commands back to the car to adjust its movement accordingly.