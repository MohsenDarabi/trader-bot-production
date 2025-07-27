#!/bin/bash
# Script to restart trading bots with updated code

echo "=== Restarting Trading Bots with Buy Order Fix ==="
echo "Timestamp: $(date)"

# Stop existing containers
echo "Stopping existing containers..."
docker-compose -f docker-compose.multi.yml down

# Rebuild with updated code
echo "Rebuilding Docker images with fixed code..."
docker-compose -f docker-compose.multi.yml build --no-cache

# Start containers
echo "Starting containers..."
docker-compose -f docker-compose.multi.yml up -d

# Wait a moment for containers to start
sleep 5

# Check status
echo -e "\n=== Container Status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"

echo -e "\n=== Resource Usage ==="
docker stats --no-stream

echo -e "\n=== Checking Logs ==="
echo "ADA Bot logs:"
docker logs --tail 10 bot-ada
echo -e "\nETH Bot logs:"
docker logs --tail 10 bot-eth

echo -e "\n✅ Bot restart complete!"
echo "Monitor logs with: docker logs -f bot-ada"