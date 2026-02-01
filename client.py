#!/usr/bin/env python3

import asyncio
import hashlib
import json
import sys
import os

try:
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.message import Message
    from solders.transaction import VersionedTransaction
    from anchorpy import Program, Provider, Wallet, Idl
    from anchorpy.program.context import Context
except ImportError:
    sys.exit(1)


PROGRAM_ID   = os.environ.get("PROGRAM_ID",  "51xxfcg4q4JS1b6dtFg6KdCSYFsTZCp3J696HK1Nsq2t")
RPC_URL      = os.environ.get("RPC_URL",     "https://api.devnet.solana.com")
KEYPAIR_PATH = os.environ.get("KEYPAIR_PATH", os.path.expanduser("./wallet-keypair.json"))
IDL_PATH     = os.environ.get("IDL_PATH",    "./idl.json")   # output of `anchor build`

SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")

def _cluster_param() -> str:
    """Map the configured RPC_URL to the ?cluster= query value."""
    url = RPC_URL.lower()
    if "devnet" in url:
        return "devnet"
    if "testnet" in url:
        return "testnet"
    return "mainnet-beta"

def explorer_tx(sig: str) -> str:
    return f"https://explorer.solana.com/tx/{sig}?cluster={_cluster_param()}"

def explorer_address(pubkey) -> str:
    return f"https://explorer.solana.com/address/{pubkey}?cluster={_cluster_param()}"

def load_keypair(path: str) -> Keypair:
    with open(path) as f:
        data = json.load(f)            # list of ints, e.g. [44, 118, …]
    return Keypair.from_bytes(bytes(data))

def load_idl(path: str) -> Idl:
    with open(path) as f:
        raw: str = f.read()          # keep as string!
    return Idl.from_json(raw)


class GrievanceClient:

    def __init__(self):
        self.keypair  = load_keypair(KEYPAIR_PATH)
        self.wallet   = Wallet(self.keypair)
        self.rpc      = AsyncClient(RPC_URL, commitment=Confirmed)
        self.provider = Provider(self.rpc, self.wallet)

        idl = load_idl(IDL_PATH)
        self.program  = Program(idl, Pubkey.from_string(PROGRAM_ID), self.provider)

        print(f"  Program loaded from IDL")
        print(f"    RPC      : {RPC_URL}")
        print(f"    Program  : {PROGRAM_ID}")
        print(f"    Wallet   : {self.keypair.pubkey()}")
        print(f"    Explorer : {explorer_address(self.keypair.pubkey())}")

    
    @staticmethod
    def _grievance_pda(user: Pubkey, message: str, program_id: Pubkey):
        """seeds = ["grievance", user_pubkey, sha256(message)]"""
        msg_hash = hashlib.sha256(message.encode()).digest()
        pda, _bump = Pubkey.find_program_address(
            [b"grievance", bytes(user), msg_hash],
            program_id,
        )
        return pda, msg_hash

    @staticmethod
    def _status_pda(grievance: Pubkey, seed: int, program_id: Pubkey):
        pda, _bump = Pubkey.find_program_address(
            [b"status", bytes(grievance), seed.to_bytes(8, "little")],
            program_id,
        )
        return pda

    
    async def register(self, cid: str, message: str, department: str):
        
        grievance_pda, msg_hash = self._grievance_pda(
            self.keypair.pubkey(), message, self.program.program_id
        )

        print(f"\n  Registering grievance…")
        print(f"    CID        : {cid}")
        print(f"    Department : {department}")
        print(f"    PDA        : {grievance_pda}")
        print(f"    Explorer   : {explorer_address(grievance_pda)}")

        existing = await self.provider.connection.get_account_info(grievance_pda)
        if existing.value is not None:
            print(f"\n  DUPLICATE — a grievance at this PDA already exists.")
            print(f"    The PDA is derived from (user + sha256(message)), so the")
            print(f"    same user + same message always maps to the same address.")
            print(f"     Existing account : {explorer_address(grievance_pda)}")
            print(f"\n    Options:")
            print(f"      • Use a different --message to create a new grievance.")
            print(f"      • Use `get --grievance {grievance_pda}` to inspect it.")
            return {"grievance_pda": str(grievance_pda), "already_exists": True}

        ctx = Context(
            accounts={
                "grievance":       grievance_pda,
                "user":            self.keypair.pubkey(),
                "system_program":  SYSTEM_PROGRAM_ID,
            }
        )

        tx_sig = await self.program.rpc["register_grievance"](
            cid,                  # String
            list(msg_hash),       # [u8; 32]  — anchorpy expects a list of ints
            department,           # String
            ctx=ctx,
        )

        print(f"  Registered!")
        print(f"    Grievance PDA : {grievance_pda}")
        print(f"    Tx      : {explorer_tx(str(tx_sig))}")
        print(f"    Account : {explorer_address(grievance_pda)}")
        return {"signature": str(tx_sig), "grievance_pda": str(grievance_pda)}

    
    async def update(
        self,
        grievance_pubkey: str,
        status: str,
        seed: int = 0,
        note_cid: str | None = None,
    ):
        
        VALID_STATUSES = ("Pending", "InProgress", "Resolved", "Rejected")
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")

        grievance_pk = Pubkey.from_string(grievance_pubkey)
        status_pda   = self._status_pda(grievance_pk, seed, self.program.program_id)

        print(f"\n  Updating status…")
        print(f"    Grievance        : {grievance_pk}")
        print(f"    New status       : {status}")
        print(f"    Seed             : {seed}")
        print(f"    StatusUpdate PDA : {status_pda}")
        print(f"    Explorer         : {explorer_address(status_pda)}")

        existing = await self.provider.connection.get_account_info(status_pda)
        if existing.value is not None:
            print(f"\n  DUPLICATE — a StatusUpdate at seed={seed} already exists.")
            print(f"    The PDA is derived from (grievance + seed), so reusing")
            print(f"    the same seed always maps to the same address.")
            print(f"     Existing account : {explorer_address(status_pda)}")
            print(f"\n    Options:")
            print(f"      • Use --seed {seed + 1} (or higher) for the next update.")
            print(f"      • Use `get-update --grievance {grievance_pk} --seed {seed}` to inspect it.")
            return {"status_update_pda": str(status_pda), "already_exists": True}

        ctx = Context(
            accounts={
                "grievance":       grievance_pk,
                "status_update":   status_pda,
                "updater":         self.keypair.pubkey(),
                "system_program":  SYSTEM_PROGRAM_ID,
            }
        )

        status_enum = getattr(self.program.type["GrievanceStatus"], status)()

        tx_sig = await self.program.rpc["update_status"](
            seed,                  # u64
            status_enum,           # GrievanceStatus enum
            note_cid,              # Option<String>  (None → None, str → Some(str))
            ctx=ctx,
        )

        print(f"  Status updated!")
        print(f"    StatusUpdate PDA : {status_pda}")
        print(f"     Tx      : {explorer_tx(str(tx_sig))}")
        print(f"     Account : {explorer_address(status_pda)}")
        return {"signature": str(tx_sig), "status_update_pda": str(status_pda)}

    async def get_grievance(self, grievance_pubkey: str):
        """
        Fetch and decode a Grievance account.

        program.account["Grievance"].fetch(pk) handles everything:
        it GETs the raw bytes, validates the 8-byte discriminator, and
        Borsh-deserialises the rest according to the IDL struct layout.
        """
        pk = Pubkey.from_string(grievance_pubkey)

        # try:
        grievance = await self.program.account["Grievance"].fetch(pk)
        # except:
        #     return None

        print(f"\n  Grievance account: {pk}")
        print(f"    CID        : {grievance.cid}")
        print(f"    Department : {grievance.department}")
        print(f"    Created by : {grievance.created_by}")
        print(f"    Created at : {grievance.created_at}")
        print(f"     Account       : {explorer_address(pk)}")
        print(f"     Created by    : {explorer_address(grievance.created_by)}")
        return grievance

    async def get_status_update(self, grievance_pubkey: str, seed: int):
        """Fetch a single StatusUpdate by its deterministic PDA."""
        grievance_pk = Pubkey.from_string(grievance_pubkey)
        status_pda   = self._status_pda(grievance_pk, seed, self.program.program_id)

        # try:
        update = await self.program.account["StatusUpdate"].fetch(status_pda)
            # print(update)
        # except Exception as e:
        #     return None

        STATUS_NAMES = {0: "Pending", 1: "InProgress", 2: "Resolved", 3: "Rejected"}
        # print(update.status.__class__.__name__)
        # status_label = STATUS_NAMES.get(update.status, str(update.status))
        status_label = update.status.__class__.__name__

        print(f"\n  StatusUpdate (seed={seed}): {status_pda}")
        print(f"    Grievance  : {update.grievance}")
        print(f"    Status     : {status_label}")
        print(f"    Updated by : {update.updated_by}")
        print(f"    Updated at : {update.updated_at}")
        if update.note_cid:
            print(f"    Note CID   : {update.note_cid}")
        print(f"     StatusUpdate  : {explorer_address(status_pda)}")
        print(f"     Grievance     : {explorer_address(update.grievance)}")
        print(f"     Updated by    : {explorer_address(update.updated_by)}")
        return update

    async def close(self):
        await self.program.close()   # closes provider → closes AsyncClient


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Grievance Portal client (anchorpy 0.21)")
    sub    = parser.add_subparsers(dest="cmd")

    reg = sub.add_parser("register", help="Submit a new grievance")
    reg.add_argument("--cid",        required=True, help="IPFS CID of encrypted data")
    reg.add_argument("--message",    required=True, help="Plain-text message (hashed for PDA seed)")
    reg.add_argument("--department", required=True, help="Department name")

    upd = sub.add_parser("update", help="Update grievance status")
    upd.add_argument("--grievance", required=True, help="Grievance account pubkey")
    upd.add_argument("--status",    required=True,
                     choices=["Pending", "InProgress", "Resolved", "Rejected"])
    upd.add_argument("--seed",      type=int, default=0,
                     help="Update index (0, 1, 2 …)")
    upd.add_argument("--note-cid",  default=None, help="Optional IPFS CID for notes")

    get = sub.add_parser("get", help="Fetch a grievance account")
    get.add_argument("--grievance", required=True, help="Grievance account pubkey")

    gu  = sub.add_parser("get-update", help="Fetch a status-update account")
    gu.add_argument("--grievance", required=True)
    gu.add_argument("--seed",      type=int, required=True)

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return

    client = GrievanceClient()
    try:
        if args.cmd == "register":
            await client.register(args.cid, args.message, args.department)

        elif args.cmd == "update":
            await client.update(args.grievance, args.status, args.seed, args.note_cid)

        elif args.cmd == "get":
            await client.get_grievance(args.grievance)

        elif args.cmd == "get-update":
            await client.get_status_update(args.grievance, args.seed)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())