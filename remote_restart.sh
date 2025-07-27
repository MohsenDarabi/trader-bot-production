#!/bin/bash
# Run restart script on VM remotely

echo "Restarting bots on VM remotely..."
ssh -i ./ssh-key-2025-07-27.key ubuntu@89.168.111.195 'cd ~/trader-bot-production && chmod +x restart_bots.sh && ./restart_bots.sh'