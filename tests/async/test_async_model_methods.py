from django.test import TestCase, TransactionTestCase

from .models import SimpleModel
from django.db import transaction, new_connection
from asgiref.sync import async_to_sync


# XXX should there be a way of catching this
# class AsyncSyncCominglingTest(TransactionTestCase):

#     available_apps = ["async"]

#     async def change_model_with_async(self, obj):
#         obj.field = 10
#         await obj.asave()

#     def test_transaction_async_comingling(self):
#         with transaction.atomic():
#             s1 = SimpleModel.objects.create(field=0)
#             async_to_sync(self.change_model_with_async)(s1)


class AsyncModelOperationTest(TransactionTestCase):

    available_apps = ["async"]

    def setUp(self):
        super().setUp()
        self.s1 = SimpleModel.objects.create(field=0)

    @TestCase.use_async_connections
    async def test_asave(self):
        from django.db.backends.utils import block_sync_ops

        with block_sync_ops():
            self.s1.field = 10
            await self.s1.asave()
            refetched = await SimpleModel.objects.aget()
            self.assertEqual(refetched.field, 10)

    async def test_adelete(self):
        await self.s1.adelete()
        count = await SimpleModel.objects.acount()
        self.assertEqual(count, 0)

    async def test_arefresh_from_db(self):
        await SimpleModel.objects.filter(pk=self.s1.pk).aupdate(field=20)
        await self.s1.arefresh_from_db()
        self.assertEqual(self.s1.field, 20)

    async def test_arefresh_from_db_from_queryset(self):
        await SimpleModel.objects.filter(pk=self.s1.pk).aupdate(field=20)
        with self.assertRaises(SimpleModel.DoesNotExist):
            await self.s1.arefresh_from_db(
                from_queryset=SimpleModel.objects.filter(field=0)
            )
        await self.s1.arefresh_from_db(
            from_queryset=SimpleModel.objects.filter(field__gt=0)
        )
        self.assertEqual(self.s1.field, 20)
