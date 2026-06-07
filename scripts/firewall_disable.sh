#!/bin/bash
set -e

RULE_CHECK=$(sudo iptables -C INPUT -p tcp --dport 8000 -s 192.168.111.0/24 -j ACCEPT 2>&1)

if [ $? -eq 0 ]; then
    sudo iptables -D INPUT -p tcp --dport 8000 -s 192.168.111.0/24 -j ACCEPT
    echo "[OK] iptables rule removed (port 8000 closed)"
else
    echo "[SKIP] iptables rule does not exist"
fi
