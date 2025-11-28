import time
from nado_protocol.client import create_nado_client
from nado_protocol.engine_client.types.execute import (
    OrderParams,
    PlaceOrderParams,
    WithdrawCollateralParams,
    CancelOrdersParams
)
from nado_protocol.contracts.types import DepositCollateralParams
from nado_protocol.utils.bytes32 import subaccount_to_bytes32, subaccount_to_hex
from nado_protocol.utils.expiration import OrderType, get_expiration_timestamp
from nado_protocol.utils.math import to_pow_10, to_x18
from nado_protocol.utils.nonce import gen_order_nonce
from nado_protocol.utils.subaccount import SubaccountParams
from nado_protocol.utils.order import build_appendix


client = MarketAPI()



print(client.market.get_all_engine_markets())
