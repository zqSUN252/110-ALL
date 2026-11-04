import pytest
from app.common import Actor, DomainError, require


def test_permission_and_warehouse_scope_are_enforced():
    actor = Actor(id='u1', capabilities={'receipts.write'}, warehouse_ids={'w1'})
    require(actor, 'receipts.write', 'w1')
    with pytest.raises(DomainError) as denied:
        require(actor, 'receipts.write', 'w2')
    assert denied.value.status == 403
    with pytest.raises(DomainError):
        require(actor, 'payments.write')
