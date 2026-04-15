"""Tests for the diagnostic script's analysis logic."""

from __future__ import annotations

from scripts.diagnose_lab import _analyze


class TestAnalyzeArista:
    """Test _analyze with Arista EOS show command output."""

    def test_bgp_config_present(self) -> None:
        commands = {
            "show running-config section router bgp": (
                "router bgp 65000\n"
                "   router-id 10.1.0.1\n"
                "   neighbor 10.1.1.1 remote-as 65001\n"
                "   address-family ipv4\n"
                "      neighbor 10.1.1.1 activate\n"
                "   address-family evpn\n"
                "      neighbor 10.1.0.11 activate\n"
            ),
            "show ip bgp summary": (
                "BGP summary information for VRF default\n"
                "Neighbor         V  AS MsgRcvd MsgSent   InQ  OutQ  Up/Down State/PfxRcd\n"
                "10.1.1.1         4 65001     100     100     0     0 00:05:00 3\n"
            ),
            "show ip interface brief": (
                "Interface         IP Address      Status   Protocol\n"
                "Ethernet1         10.1.1.0/31     up       up\n"
                "Management1       192.168.68.110/22 up     up\n"
            ),
            "show interfaces status": "",
            "show ip route summary": "",
        }
        result = _analyze("dc-spine-1", "arista_eos", commands)
        assert result["bgp_config_present"] is True
        assert result["af_ipv4_present"] is True
        assert result["af_evpn_present"] is True
        assert result["configured_neighbors"] == 1
        assert result["bgp_total_peers"] == 1
        assert result["bgp_established"] == 1
        assert result["bgp_down"] == 0

    def test_bgp_config_missing(self) -> None:
        commands = {
            "show running-config section router bgp": "",
            "show ip bgp summary": "% BGP is not running",
            "show ip interface brief": "",
            "show interfaces status": "",
            "show ip route summary": "",
        }
        result = _analyze("dc-spine-1", "arista_eos", commands)
        assert result["bgp_config_present"] is False
        assert result["bgp_total_peers"] == 0
        assert result["configured_neighbors"] == 0

    def test_bgp_peers_down(self) -> None:
        commands = {
            "show running-config section router bgp": (
                "router bgp 65000\n   neighbor 10.1.1.1 remote-as 65001\n"
            ),
            "show ip bgp summary": (
                "Neighbor         V  AS MsgRcvd MsgSent   InQ  OutQ  Up/Down State/PfxRcd\n"
                "10.1.1.1         4 65001       0       0     0     0 never   Active\n"
                "10.1.1.3         4 65002       0       0     0     0 never   Idle\n"
            ),
            "show ip interface brief": "",
            "show interfaces status": "",
            "show ip route summary": "",
        }
        result = _analyze("dc-spine-1", "arista_eos", commands)
        assert result["bgp_total_peers"] == 2
        assert result["bgp_established"] == 0
        assert result["bgp_down"] == 2

    def test_eos_433_description_first_format(self) -> None:
        """EOS 4.33+ puts Description before Neighbor IP in BGP summary."""
        commands = {
            "show running-config section router bgp": (
                "router bgp 65000\n"
                "   neighbor 10.1.1.1 remote-as 65001\n"
                "   address-family ipv4\n"
                "      neighbor 10.1.1.1 activate\n"
                "   address-family evpn\n"
            ),
            "show ip bgp summary": (
                "BGP summary information for VRF default\n"
                "Router identifier 10.1.0.1, local AS number 65000\n"
                "Neighbor Status Codes: m - Under maintenance\n"
                "  Description              Neighbor V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc\n"
                "  dc-leaf-1                10.1.1.1 4 65001             11        14    0    0 00:05:17 Estab   1      1\n"
                "  dc-leaf-2                10.1.1.3 4 65002             11        13    0    0 00:05:18 Estab   1      1\n"
                "  dc-border-1              10.1.1.5 4 65003             11        13    0    0 00:05:18 Estab   3      3\n"
                "  dc-border-2              10.1.1.7 4 65004             11        13    0    0 00:05:17 Estab   3      3\n"
            ),
            "show ip interface brief": "",
            "show interfaces status": "",
            "show ip route summary": "",
        }
        result = _analyze("dc-spine-1", "arista_eos", commands)
        assert result["bgp_total_peers"] == 4
        assert result["bgp_established"] == 4
        assert result["bgp_down"] == 0


class TestAnalyzeCisco:
    """Test _analyze with Cisco IOS-XE show command output."""

    def test_bgp_established(self) -> None:
        commands = {
            "show running-config | section router bgp": (
                "router bgp 65100\n bgp router-id 172.16.0.101\n"
            ),
            "show ip bgp summary": (
                "Neighbor        V   AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd\n"
                "172.16.0.1      4 64500     200     200       10    0    0 01:00:00        2\n"
                "172.16.0.3      4 64500     200     200       10    0    0 01:00:00        2\n"
            ),
            "show ip interface brief": (
                "Interface              IP-Address      OK? Method Status Protocol\n"
                "GigabitEthernet1       172.16.0.0      YES manual up     up\n"
                "GigabitEthernet5       192.168.68.120  YES manual up     up\n"
            ),
            "show ip route summary": "",
        }
        result = _analyze("dc-ce-1", "cisco_xe", commands)
        assert result["bgp_config_present"] is True
        assert result["bgp_total_peers"] == 2
        assert result["bgp_established"] == 2
