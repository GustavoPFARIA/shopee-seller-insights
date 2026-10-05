"""Helpers to build Shopee-like order export files for tests."""

from dataclasses import dataclass

HEADER = [
    "ID do pedido",
    "Status do pedido",
    "Data de criação do pedido",
    "Número de referência SKU",
    "Nome do Produto",
    "Preço acordado",
    "Quantidade",
    "Taxa de comissão",
    "Taxa de serviço",
    "Taxa de envio paga pelo vendedor",
    "Cupom do vendedor",
    "Nome de usuário (comprador)",
    "Nome do destinatário",
    "Telefone",
    "Endereço de entrega",
]


@dataclass
class Line:
    order_sn: str
    sku: str
    price: str
    qty: int = 1
    status: str = "Concluído"
    date: str = "2025-09-10 12:00"
    name: str | None = None
    commission: str = "0.00"
    service: str = "0.00"
    shipping: str = "0.00"
    voucher: str = "0.00"
    buyer: str = "buyer_x"


def build_csv(lines: list[Line]) -> bytes:
    out = [",".join(HEADER)]
    for ln in lines:
        out.append(
            ",".join(
                [
                    ln.order_sn,
                    ln.status,
                    ln.date,
                    ln.sku,
                    ln.name or f"Product {ln.sku}",
                    ln.price,
                    str(ln.qty),
                    ln.commission,
                    ln.service,
                    ln.shipping,
                    ln.voucher,
                    ln.buyer,
                    "Joao da Silva",
                    "+55 11 98888-7777",
                    "Rua Secreta 42",
                ]
            )
        )
    return ("\n".join(out) + "\n").encode()
