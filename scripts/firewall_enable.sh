#!/bin/bash
set -e

RULE_CHECK=$(sudo iptables -C INPUT -p tcp --dport 8000 -s 192.168.111.0/24 -j ACCEPT 2>&1)

if [ $? -ne 0 ]; then
    sudo iptables -A INPUT -p tcp --dport 8000 -s 192.168.111.0/24 -j ACCEPT
    echo "[OK] iptables rule added (port 8000 open for local network)"
else
    echo "[SKIP] iptables rule already exists"
fi
