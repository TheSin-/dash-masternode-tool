#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Bertrand256
# Created on: 2017-03

import json
import binascii
import logging
from keepkeylib.client import TextUIMixin as keepkey_TextUIMixin
from keepkeylib.client import ProtocolMixin as keepkey_ProtocolMixin
from keepkeylib.client import BaseClient as keepkey_BaseClient
from keepkeylib import messages_pb2 as keepkey_proto
from keepkeylib.tx_api import TxApiInsight
from hw_common import HardwareWalletCancelException
import keepkeylib.types_pb2 as proto_types
from wnd_utils import WndUtils
from hw_common import clean_bip32_path


class MyKeepkeyTextUIMixin(keepkey_TextUIMixin):

    def __init__(self, transport, ask_for_pin_fun, ask_for_pass_fun):
        keepkey_TextUIMixin.__init__(self, transport)
        self.ask_for_pin_fun = ask_for_pin_fun
        self.ask_for_pass_fun = ask_for_pass_fun

    def callback_PassphraseRequest(self, msg):
        passphrase = self.ask_for_pass_fun(msg)
        if passphrase is None:
            raise HardwareWalletCancelException('Cancelled')
        return keepkey_proto.PassphraseAck(passphrase=passphrase)

    def callback_PinMatrixRequest(self, msg):
        if msg.type == 1:
            desc = 'Enter current PIN'
        elif msg.type == 2:
            desc = 'Enter new PIN'
        elif msg.type == 3:
            desc = 'Enter new PIN again'
        else:
            desc = 'Enter PIN'
        pin = self.ask_for_pin_fun(desc)
        if not pin:
            raise HardwareWalletCancelException('Cancelled')
        return keepkey_proto.PinMatrixAck(pin=pin)


class MyKeepkeyClient(keepkey_ProtocolMixin, MyKeepkeyTextUIMixin, keepkey_BaseClient):
    def __init__(self, transport, ask_for_pin_fun, ask_for_pass_fun):
        keepkey_ProtocolMixin.__init__(self, transport, ask_for_pin_fun, ask_for_pass_fun)
        MyKeepkeyTextUIMixin.__init__(self, transport, ask_for_pin_fun, ask_for_pass_fun)
        keepkey_BaseClient.__init__(self, transport)


def connect_keepkey(ask_for_pin_fun, ask_for_pass_fun):
    """
    Connect to a Keepkey device.
    :param ask_for_pin_fun: ref to a function displaying a dialog asking the user for a pin (Trezor and Keepkey)
    :param ask_for_pass_fun: ref to a function displaying a dialog asking the user for a passphrase (Trezor and Keepkey)
    :return: ref to a keepkey client if connection successfull or None if we are sure that no Keepkey device connected.
    """

    def get_transport():
        from keepkeylib.transport_hid import HidTransport
        count = len(HidTransport.enumerate())
        if not count:
            logging.warning('Number of Keepkey devices: 0')
        for d in HidTransport.enumerate():
            transport = HidTransport(d)
            return transport

    # HidTransport.enumerate() has to be called in the main thread - second call from bg thread
    # causes SIGSEGV
    transport = WndUtils.callFunInTheMainThread(get_transport)

    if transport:
        client = MyKeepkeyClient(transport, ask_for_pin_fun, ask_for_pass_fun)
        return client
    else:
        logging.warning('Transport is None')


def reconnect_keepkey(client, ask_for_pin_fun, ask_for_pass_fun):
    try:
        from trezorlib.transport_hid import HidTransport
        client.init_device()
        return connect_keepkey(ask_for_pin_fun, ask_for_pass_fun)
    except Exception as e:
        logging.exception("Exception occurred")
        raise


class MyTxApiInsight(TxApiInsight):

    def __init__(self, network, url, terracoind_inf, cache_dir, zcash=None):
        TxApiInsight.__init__(self, network, url, zcash)
        self.terracoind_inf = terracoind_inf
        self.cache_dir = cache_dir

    def fetch_json(self, url, resource, resourceid):
        cache_file = ''
        if self.cache_dir:
            cache_file = '%s/%s_%s_%s.json' % (self.cache_dir, self.network, resource, resourceid)
            try: # looking into cache first
                j = json.load(open(cache_file))
                return j
            except:
                pass
        try:
            j = self.terracoind_inf.getrawtransaction(resourceid.decode("utf-8"), 1)
        except Exception as e:
            raise
        if cache_file:
            try: # saving into cache
                json.dump(j, open(cache_file, 'w'))
            except Exception as e:
                pass
        return j


def prepare_transfer_tx(main_ui, utxos_to_spend, dest_address, tx_fee):
    """
    Creates a signed transaction.
    :param main_ui: Main window for configuration data
    :param utxos_to_spend: list of utxos to send
    :param dest_address: destination (Terracoin) address
    :param tx_fee: transaction fee
    :return: tuple (serialized tx, total transaction amount in satoshis)
    """
    tx_api = MyTxApiInsight('insight_terracoin', None, main_ui.terracoind_intf, main_ui.config.cache_dir)
    client = main_ui.hw_client
    client.set_tx_api(tx_api)
    inputs = []
    outputs = []
    amt = 0
    for utxo in utxos_to_spend:
        if not utxo.get('bip32_path', None):
            raise Exception('No BIP32 path for UTXO ' + utxo['txid'])
        address_n = client.expand_path(clean_bip32_path(utxo['bip32_path']))
        it = proto_types.TxInputType(address_n=address_n, prev_hash=binascii.unhexlify(utxo['txid']),
                                     prev_index=utxo['outputIndex'])
        inputs.append(it)
        amt += utxo['satoshis']
    amt -= tx_fee
    amt = int(amt)

    # check if dest_address is a Terracoin address or a script address and then set appropriate script_type
    # https://github.com/terracoin/terracoin/blob/master/src/chainparams.cpp#L361
    if dest_address.startswith('3'):
        stype = proto_types.PAYTOSCRIPTHASH
    else:
        stype = proto_types.PAYTOADDRESS

    ot = proto_types.TxOutputType(
        address=dest_address,
        amount=amt,
        script_type=stype
    )
    outputs.append(ot)
    signed = client.sign_tx('Terracoin', inputs, outputs)
    return signed[1], amt


def sign_message(main_ui, bip32path, message):
    client = main_ui.hw_client
    address_n = client.expand_path(clean_bip32_path(bip32path))
    return client.sign_message('Terracoin', address_n, message)


def change_pin(main_ui, remove=False):
    if main_ui.hw_client:
        main_ui.hw_client.change_pin(remove)
    else:
        raise Exception('HW client not set.')


def apply_settings(main_ui, label=None, language=None, use_passphrase=None, homescreen=None):
    if main_ui.hw_client:
        main_ui.hw_client.apply_settings()
    else:
        raise Exception('HW client not set.')
