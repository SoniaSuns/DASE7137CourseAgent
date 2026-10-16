"""Explicit LAN opt-in; never bind the workspace to every network interface."""
from ipaddress import IPv4Address, IPv4Network

PRIVATE = tuple(IPv4Network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def lan_config(config):
    address, subnet = config.get('lan_address', ''), config.get('lan_subnet', '')
    if not address and not subnet:
        return None
    ip, network = IPv4Address(address), IPv4Network(subnet, strict=True)
    if not any(network.subnet_of(block) for block in PRIVATE) or ip not in network:
        raise ValueError('LAN address and subnet must belong to the same private IPv4 network.')
    if network.prefixlen < 31 and ip in (network.network_address, network.broadcast_address):
        raise ValueError('Choose an individual LAN interface address.')
    return str(ip), network


def allowed_peer(address, lan):
    try:
        ip = IPv4Address(address)
        return ip == IPv4Address('127.0.0.1') or bool(lan and ip in lan[1])
    except ValueError:
        return False
