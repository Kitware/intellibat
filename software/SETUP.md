# Setting up an Intellibat Device

### setup.sh
Included in this repository is the `setup.sh` script. This handles setting up a new Intellibat device to run its recording and data processing, as well as set up an access point for live device monitoring and configuration.

### How to run the script
The script uses environment variables to set the device name (used as the access point name and to name files) and the access point password. The default value for the device name is `Intellibat` and the default password is `intellibat123`. In order to set the device up with a unique name and password, set the following environment variables *before* running the `setup.sh` script:

```bash
export INTELLIBAT_DEVICE_NAME="My Intellibat Device"
export INTELLIBAT_AP_PASSWORD="my unique password"
```

You will use the password to connect to the Raspberry pi as a wifi device. Make sure it is easy to remember.

### Script details
The script will create a new user on the system, `intellibat`. This user will own the files responsible for running the services and the configuration. The working directory for the recording and monitoring services is `/opt/intellibat`, which is created as part of the script. This directory contains the source code and python environment used for the intellibat services.

The script also creates `/etc/intellibat`, which contains the configuration information for the services. The default configuration and device name file are copied here by the setup script.

The script then creates an access point that you can use to connect to via a wifi-enabled device such as a smart phone or laptop. When enabled, the connection will appear as a wifi network that you can connect to using the device name set up by the script (see above). By default, the script does not enable the access point. To enable the access point, run:

```bash
# use the intellibat acccess point. While this is up,
# regular wifi is disabled on the raspberry pi.
sudo nmcli connection up intellibat-ap

# If you wish to connect to wifi from the raspberry pi again,
# simply bring this connection down and the default wifi
# connection will take over
sudo nmcli connection down intellibat-ap
```

Lastly, the setup script copies, enables, and restarts the services `intellibat`, which is the recorder, and `intellibat_config`, which serves a local-network-only web application for device monitoring and configuration
