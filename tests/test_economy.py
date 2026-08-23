"""Automated tests for Sovereign Autonomous Monetary & Compute Economy Layer.

Tests NodeWallet, Ledger, StateChannel, ComputeMarket, SettlementEngine,
and smdc economy CLI subcommands.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from sovereign_dc.cli import (
    cmd_economy,
    cmd_economy_history,
    cmd_economy_market,
    cmd_economy_send,
    cmd_economy_wallet,
)
from sovereign_dc.economy import (
    AddressType,
    ComputeMarket,
    Ledger,
    NodeWallet,
    ProofOfCompute,
    ProofOfRelay,
    ServiceOffer,
    ServiceType,
    SettlementEngine,
    StateChannel,
)


class TestNodeWallet:
    """Test suite for NodeWallet keypair management and signing."""

    def test_create_ed25519_wallet(self) -> None:
        wallet = NodeWallet.create(node_id="smdc-node-alpha", algorithm=AddressType.ED25519)
        assert wallet.node_id == "smdc-node-alpha"
        assert wallet.address.startswith("sov_")
        assert wallet.keypair.algorithm == AddressType.ED25519
        assert len(wallet.keypair.public_key_hex) > 0

    def test_create_pqc_wallet(self) -> None:
        wallet = NodeWallet.create(node_id="smdc-node-pqc", algorithm=AddressType.ML_DSA_87)
        assert wallet.node_id == "smdc-node-pqc"
        assert wallet.address.startswith("sov_pqc_")
        assert wallet.keypair.algorithm == AddressType.ML_DSA_87
        assert len(wallet.keypair.public_key_hex) > 0

    def test_sign_and_verify_ed25519(self) -> None:
        wallet = NodeWallet.create(node_id="node-1", algorithm=AddressType.ED25519)
        msg = b"TRANSFER_100_CREDITS"
        sig = wallet.sign_payload(msg)

        assert NodeWallet.verify_signature(
            payload=msg,
            signature_hex=sig,
            public_key_hex=wallet.keypair.public_key_hex,
            algorithm=AddressType.ED25519,
        )
        assert not NodeWallet.verify_signature(
            payload=b"TAMPERED_MSG",
            signature_hex=sig,
            public_key_hex=wallet.keypair.public_key_hex,
            algorithm=AddressType.ED25519,
        )

    def test_sign_and_verify_pqc(self) -> None:
        wallet = NodeWallet.create(node_id="node-pqc", algorithm=AddressType.ML_DSA_87)
        msg = b"SPACE_DTN_SETTLEMENT_BUNDLE_HASH"
        sig = wallet.sign_payload(msg)

        assert NodeWallet.verify_signature(
            payload=msg,
            signature_hex=sig,
            public_key_hex=wallet.keypair.public_key_hex,
            algorithm=AddressType.ML_DSA_87,
        )

    def test_save_and_load_wallet(self, tmp_path) -> None:
        wallet_file = str(tmp_path / "test_wallet.json")
        wallet = NodeWallet.create(node_id="saved-node", algorithm=AddressType.ED25519)
        wallet.save_to_file(wallet_file)

        loaded = NodeWallet.load_from_file(wallet_file)
        assert loaded.node_id == "saved-node"
        assert loaded.address == wallet.address
        assert loaded.keypair.public_key_hex == wallet.keypair.public_key_hex


class TestLedgerAndTransactions:
    """Test suite for append-only ledger, transfers, and replay protection."""

    def test_mint_and_balance(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        wallet = NodeWallet.create(node_id="node-1")

        assert ledger.get_balance(wallet.address) == 0.0
        tx = ledger.mint(recipient=wallet.address, amount=250.0, memo="TEST_MINT")

        assert tx.sender == "MINT"
        assert tx.amount == 250.0
        assert ledger.get_balance(wallet.address) == 250.0

    def test_transfer_between_wallets(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        alice = NodeWallet.create(node_id="alice")
        bob = NodeWallet.create(node_id="bob")

        ledger.mint(alice.address, 100.0)
        tx = ledger.transfer(sender_wallet=alice, recipient=bob.address, amount=40.0, memo="GPU_COMPUTE_PAYMENT")

        assert tx.sender == alice.address
        assert tx.recipient == bob.address
        assert tx.amount == 40.0
        assert tx.is_valid(alice.keypair.public_key_hex)
        assert ledger.get_balance(alice.address) == 60.0
        assert ledger.get_balance(bob.address) == 40.0
        assert ledger.get_nonce(alice.address) == 1

    def test_insufficient_balance_rejection(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        alice = NodeWallet.create(node_id="alice")
        bob = NodeWallet.create(node_id="bob")

        ledger.mint(alice.address, 20.0)
        with pytest.raises(ValueError, match="Insufficient balance"):
            ledger.transfer(sender_wallet=alice, recipient=bob.address, amount=50.0)

    def test_self_transfer_rejection(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        alice = NodeWallet.create(node_id="alice")
        ledger.mint(alice.address, 50.0)

        with pytest.raises(ValueError, match="Cannot transfer credits to self"):
            ledger.transfer(sender_wallet=alice, recipient=alice.address, amount=10.0)

    def test_ledger_history_and_export(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        alice = NodeWallet.create(node_id="alice")
        bob = NodeWallet.create(node_id="bob")

        ledger.mint(alice.address, 100.0)
        ledger.transfer(sender_wallet=alice, recipient=bob.address, amount=25.0)
        ledger.transfer(sender_wallet=alice, recipient=bob.address, amount=15.0)

        history = ledger.get_history(address=alice.address)
        assert len(history) == 3
        state = ledger.export_state()
        assert state["total_transactions"] == 3
        assert alice.address in state["balances"]


class TestStateChannels:
    """Test suite for offline micropayment state channels."""

    def test_state_channel_lifecycle(self) -> None:
        alice = NodeWallet.create(node_id="alice")
        bob = NodeWallet.create(node_id="bob")
        channel = StateChannel(
            channel_id="chan_01",
            sender_address=alice.address,
            peer_address=bob.address,
            deposit_amount=50.0,
        )

        p1 = channel.stream_micropayment(amount=5.0, wallet=alice)
        assert p1.sequence == 1
        assert p1.amount_transferred == 5.0

        p2 = channel.stream_micropayment(amount=10.0, wallet=alice)
        assert p2.sequence == 2
        assert p2.amount_transferred == 15.0

        settled, refund = channel.close()
        assert settled == 15.0
        assert refund == 35.0
        assert channel.is_closed

    def test_state_channel_exhaustion_raises(self) -> None:
        alice = NodeWallet.create(node_id="alice")
        bob = NodeWallet.create(node_id="bob")
        channel = StateChannel(
            channel_id="chan_02",
            sender_address=alice.address,
            peer_address=bob.address,
            deposit_amount=10.0,
        )
        channel.stream_micropayment(amount=8.0, wallet=alice)
        with pytest.raises(ValueError, match="collateral exhausted"):
            channel.stream_micropayment(amount=5.0, wallet=alice)


class TestComputeMarket:
    """Test suite for dynamic solar-aware pricing and offers."""

    def test_solar_surplus_discount(self) -> None:
        market = ComputeMarket()
        mult, status = market.get_dynamic_multiplier(battery_soc=90.0, solar_power_w=1200.0)
        assert mult == 0.50
        assert "DISCOUNT" in status

        quote = market.calculate_quote(ServiceType.LLM_INFERENCE, quantity=10.0, battery_soc=90.0, solar_power_w=1200.0)
        assert quote.final_unit_price == 0.025
        assert quote.total_cost_credits == 0.25

    def test_low_battery_surge(self) -> None:
        market = ComputeMarket()
        mult, status = market.get_dynamic_multiplier(battery_soc=20.0, solar_power_w=50.0)
        assert mult == 3.00
        assert "CRITICAL" in status

    def test_market_offer_registration(self) -> None:
        market = ComputeMarket()
        offer = ServiceOffer(
            offer_id="off_llm_1",
            node_id="smdc-athens-01",
            wallet_address="sov_1234",
            service_type=ServiceType.LLM_INFERENCE.value,
            unit_price=0.04,
            unit_name="1k tokens",
            max_capacity=500.0,
        )
        market.register_offer(offer)
        offers = market.list_offers(ServiceType.LLM_INFERENCE)
        assert len(offers) == 1
        assert offers[0].unit_price == 0.04

        cancelled = market.cancel_offer("off_llm_1")
        assert cancelled
        assert len(market.list_offers(active_only=True)) == 0


class TestSettlementEngine:
    """Test suite for ProofOfCompute, ProofOfRelay, and DTN bundle settlement."""

    def test_proof_of_compute_settlement(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        client = NodeWallet.create(node_id="client")
        worker = NodeWallet.create(node_id="worker")

        ledger.mint(client.address, 100.0)

        proof = ProofOfCompute(
            task_id="task_rag_01",
            service_type="llm_inference",
            units_processed=5.0,
            client_address=client.address,
            worker_node_id=worker.node_id,
            worker_address=worker.address,
            total_credits_due=0.25,
            result_digest="sha256_output_digest",
        )
        proof.sign(worker)
        assert proof.verify(worker.keypair.public_key_hex)

        tx = SettlementEngine.settle_proof_of_compute(proof, client, ledger, worker.keypair.public_key_hex)
        assert tx.amount == 0.25
        assert ledger.get_balance(worker.address) == 0.25
        assert ledger.get_balance(client.address) == 99.75

    def test_proof_of_relay_receipt(self) -> None:
        relay = NodeWallet.create(node_id="relay")
        proof = ProofOfRelay(
            bundle_id="bundle_space_99",
            source_eid="dtn://node-1.space",
            destination_eid="dtn://ground.earth",
            bytes_relayed=1048576,
            relay_node_id=relay.node_id,
            relay_address=relay.address,
            credits_due=0.10,
        )
        proof.sign(relay)
        assert proof.verify(relay.keypair.public_key_hex)

    def test_dtn_settlement_bundle_pack_and_reconcile(self, tmp_path) -> None:
        db_file = str(tmp_path / "ledger.db")
        ledger = Ledger(db_path=db_file)
        alice = NodeWallet.create(node_id="alice")
        bob = NodeWallet.create(node_id="bob")

        ledger.mint(alice.address, 100.0)
        tx = ledger.transfer(alice, bob.address, 12.50)

        bundle = SettlementEngine.create_dtn_settlement_bundle(
            source_eid="dtn://node-alpha.space",
            destination_eid="dtn://node-beta.space",
            transactions=[tx],
            wallet=alice,
        )
        assert bundle.source_eid == "dtn://node-alpha.space"

        # Reconcile on recipient ledger
        recip_db = str(tmp_path / "recip_ledger.db")
        recip_ledger = Ledger(db_path=recip_db)
        reconciled = SettlementEngine.reconcile_dtn_settlement_bundle(bundle, recip_ledger)
        assert len(reconciled) == 1
        assert reconciled[0].tx_id == tx.tx_id


class TestEconomyCLI:
    """Test suite for smdc economy CLI commands."""

    def test_cmd_economy_wallet_create(self, tmp_path, capsys) -> None:
        wallet_file = str(tmp_path / "cli_wallet.json")
        db_file = str(tmp_path / "cli_ledger.db")
        args = Namespace(
            create=True,
            pqc=False,
            mint=50.0,
            node_id="cli-test-node",
            wallet_file=wallet_file,
            db_path=db_file,
        )
        cmd_economy_wallet(args)
        out = capsys.readouterr().out
        assert "Sovereign Node Cryptographic Wallet" in out
        assert "cli-test-node" in out
        assert "SMDC-Credits" in out

    def test_cmd_economy_send_and_history(self, tmp_path, capsys) -> None:
        wallet_file = str(tmp_path / "cli_wallet.json")
        db_file = str(tmp_path / "cli_ledger.db")
        # Init wallet
        args_w = Namespace(
            create=True,
            pqc=False,
            mint=100.0,
            node_id="sender-node",
            wallet_file=wallet_file,
            db_path=db_file,
        )
        cmd_economy_wallet(args_w)

        # Send
        args_send = Namespace(
            recipient="sov_recipient_123456",
            amount=25.0,
            memo="INFERENCE_PAYMENT",
            wallet_file=wallet_file,
            db_path=db_file,
        )
        cmd_economy_send(args_send)
        out_send = capsys.readouterr().out
        assert "Transfer of 25.00 credits completed" in out_send

        # History
        args_hist = Namespace(
            limit=10,
            db_path=db_file,
        )
        cmd_economy_history(args_hist)
        out_hist = capsys.readouterr().out
        assert "Compute Ledger Transaction History" in out_hist
        assert "25.00 credits" in out_hist

    def test_cmd_economy_market(self, capsys) -> None:
        args = Namespace(soc=85.0, solar=900.0)
        cmd_economy_market(args)
        out = capsys.readouterr().out
        assert "Sovereign Compute & Energy Marketplace" in out
        assert "llm_inference" in out
        assert "DISCOUNT" in out

    def test_cmd_economy_main_dispatcher(self, tmp_path, capsys) -> None:
        wallet_file = str(tmp_path / "cli_wallet.json")
        db_file = str(tmp_path / "cli_ledger.db")
        args = Namespace(
            create=False,
            pqc=False,
            mint=None,
            node_id="dispatcher-node",
            wallet_file=wallet_file,
            db_path=db_file,
        )
        cmd_economy(args)
        out = capsys.readouterr().out
        assert "Sovereign Node Cryptographic Wallet" in out
