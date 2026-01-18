import json
import xml.etree.ElementTree
from datetime import datetime

from asgiref.sync import async_to_sync, sync_to_async

from django.db import NotSupportedError, connection, new_connection
from django.db.models import Prefetch, Sum
from django.test import (
    TransactionTestCase,
    TestCase,
    skipIfDBFeature,
    skipUnlessDBFeature,
)

from .models import RelatedModel, SimpleModel


class AsyncQuerySetTest(TransactionTestCase):
    available_apps = ["async"]

    def setUp(self):
        self.s1 = SimpleModel.objects.create(
            field=1,
            created=datetime(2022, 1, 1, 0, 0, 0),
        )
        self.s2 = SimpleModel.objects.create(
            field=2,
            created=datetime(2022, 1, 1, 0, 0, 1),
        )
        self.s3 = SimpleModel.objects.create(
            field=3,
            created=datetime(2022, 1, 1, 0, 0, 2),
        )
        self.r1 = RelatedModel.objects.create(simple=self.s1)
        self.r2 = RelatedModel.objects.create(simple=self.s2)
        self.r3 = RelatedModel.objects.create(simple=self.s3)

    @staticmethod
    def _get_db_feature(connection_, feature_name):
        # Wrapper to avoid accessing connection attributes until inside
        # coroutine function. Connection access is thread sensitive and cannot
        # be passed across sync/async boundaries.
        return getattr(connection_.features, feature_name)

    async def test_async_iteration(self):
        results = []
        async for m in SimpleModel.objects.order_by("pk"):
            results.append(m)
        self.assertEqual(results, [self.s1, self.s2, self.s3])

    async def test_aiterator(self):
        qs = SimpleModel.objects.aiterator()
        results = []
        async for m in qs:
            results.append(m)
        self.assertCountEqual(results, [self.s1, self.s2, self.s3])

    async def test_aiterator_prefetch_related(self):
        results = []
        async for s in SimpleModel.objects.prefetch_related(
            Prefetch("relatedmodel_set", to_attr="prefetched_relatedmodel")
        ).aiterator():
            results.append(s.prefetched_relatedmodel)
        self.assertCountEqual(results, [[self.r1], [self.r2], [self.r3]])

    async def test_aiterator_invalid_chunk_size(self):
        msg = "Chunk size must be strictly positive."
        for size in [0, -1]:
            qs = SimpleModel.objects.aiterator(chunk_size=size)
            with self.subTest(size=size), self.assertRaisesMessage(ValueError, msg):
                async for m in qs:
                    pass

    async def test_acount(self):
        count = await SimpleModel.objects.acount()
        self.assertEqual(count, 3)

    async def test_acount_cached_result(self):
        qs = SimpleModel.objects.all()
        # Evaluate the queryset to populate the query cache.
        [x async for x in qs]
        count = await qs.acount()
        self.assertEqual(count, 3)

        await sync_to_async(SimpleModel.objects.create)(
            field=4,
            created=datetime(2022, 1, 1, 0, 0, 0),
        )
        # The query cache is used.
        count = await qs.acount()
        self.assertEqual(count, 3)

    async def test_aget(self):
        instance = await SimpleModel.objects.aget(field=1)
        self.assertEqual(instance, self.s1)
        with self.assertRaises(SimpleModel.MultipleObjectsReturned):
            await SimpleModel.objects.aget()
        with self.assertRaises(SimpleModel.DoesNotExist):
            await SimpleModel.objects.aget(field=98)

    async def test_acreate(self):
        await SimpleModel.objects.acreate(field=4)
        self.assertEqual(await SimpleModel.objects.acount(), 4)

    async def test_aget_or_create(self):
        instance, created = await SimpleModel.objects.aget_or_create(field=4)
        self.assertEqual(await SimpleModel.objects.acount(), 4)
        self.assertIs(created, True)

    async def test_aupdate_or_create(self):
        instance, created = await SimpleModel.objects.aupdate_or_create(
            id=self.s1.id, defaults={"field": 2}
        )
        self.assertEqual(instance, self.s1)
        self.assertEqual(instance.field, 2)
        self.assertIs(created, False)
        instance, created = await SimpleModel.objects.aupdate_or_create(field=4)
        self.assertEqual(await SimpleModel.objects.acount(), 4)
        self.assertIs(created, True)
        instance, created = await SimpleModel.objects.aupdate_or_create(
            field=5, defaults={"field": 7}, create_defaults={"field": 6}
        )
        self.assertEqual(await SimpleModel.objects.acount(), 5)
        self.assertIs(created, True)
        self.assertEqual(instance.field, 6)

    def ensure_feature(self, *args):
        if not all(getattr(connection.features, feature, False) for feature in args):
            self.skipTest(f"Database doesn't support feature(s): {', '.join(args)}")

    def skip_if_feature(self, *args):
        if any(getattr(connection.features, feature, False) for feature in args):
            self.skipTest(f"Database supports feature(s): {', '.join(args)}")

    async def test_abulk_create(self):
        self.ensure_feature("has_bulk_insert")
        instances = [SimpleModel(field=i) for i in range(10)]
        qs = await SimpleModel.objects.abulk_create(instances)
        self.assertEqual(len(qs), 10)

    async def test_update_conflicts_unique_field_unsupported(self):
        self.ensure_feature("has_bulk_insert", "support_update_conflicts")
        self.skip_if_feature("supports_update_conflicts_with_target")
        msg = (
            "This database backend does not support updating conflicts with specifying "
            "unique fields that can trigger the upsert."
        )
        with self.assertRaisesMessage(NotSupportedError, msg):
            await SimpleModel.objects.abulk_create(
                [SimpleModel(field=1), SimpleModel(field=2)],
                update_conflicts=True,
                update_fields=["field"],
                unique_fields=["created"],
            )

    async def test_abulk_update(self):
        instances = SimpleModel.objects.all()
        async for instance in instances:
            instance.field = instance.field * 10

        await SimpleModel.objects.abulk_update(instances, ["field"])

        qs = [(o.pk, o.field) async for o in SimpleModel.objects.all()]
        self.assertCountEqual(
            qs,
            [(self.s1.pk, 10), (self.s2.pk, 20), (self.s3.pk, 30)],
        )

    async def test_ain_bulk(self):
        res = await SimpleModel.objects.ain_bulk()
        self.assertEqual(
            res,
            {self.s1.pk: self.s1, self.s2.pk: self.s2, self.s3.pk: self.s3},
        )

        res = await SimpleModel.objects.ain_bulk([self.s2.pk])
        self.assertEqual(res, {self.s2.pk: self.s2})

        res = await SimpleModel.objects.ain_bulk([self.s2.pk], field_name="id")
        self.assertEqual(res, {self.s2.pk: self.s2})

    async def test_alatest(self):
        instance = await SimpleModel.objects.alatest("created")
        self.assertEqual(instance, self.s3)

        instance = await SimpleModel.objects.alatest("-created")
        self.assertEqual(instance, self.s1)

    async def test_aearliest(self):
        instance = await SimpleModel.objects.aearliest("created")
        self.assertEqual(instance, self.s1)

        instance = await SimpleModel.objects.aearliest("-created")
        self.assertEqual(instance, self.s3)

    async def test_afirst(self):
        instance = await SimpleModel.objects.afirst()
        self.assertEqual(instance, self.s1)

        instance = await SimpleModel.objects.filter(field=4).afirst()
        self.assertIsNone(instance)

    async def test_alast(self):
        instance = await SimpleModel.objects.alast()
        self.assertEqual(instance, self.s3)

        instance = await SimpleModel.objects.filter(field=4).alast()
        self.assertIsNone(instance)

    async def test_aaggregate(self):
        total = await SimpleModel.objects.aaggregate(total=Sum("field"))
        self.assertEqual(total, {"total": 6})

    async def test_aexists(self):
        check = await SimpleModel.objects.filter(field=1).aexists()
        self.assertIs(check, True)

        check = await SimpleModel.objects.filter(field=4).aexists()
        self.assertIs(check, False)

    async def test_acontains(self):
        check = await SimpleModel.objects.acontains(self.s1)
        self.assertIs(check, True)
        # Unsaved instances are not allowed, so use an ID known not to exist.
        check = await SimpleModel.objects.acontains(
            SimpleModel(id=self.s3.id + 1, field=4)
        )
        self.assertIs(check, False)

    async def test_aupdate(self):
        await SimpleModel.objects.aupdate(field=99)
        qs = [o async for o in SimpleModel.objects.all()]
        values = [instance.field for instance in qs]
        self.assertEqual(set(values), {99})

    async def test_adelete(self):
        await SimpleModel.objects.filter(field=2).adelete()
        qs = [o async for o in SimpleModel.objects.all()]
        self.assertCountEqual(qs, [self.s1, self.s3])

    async def test_aexplain(self):
        self.ensure_feature("supports_explaining_query_execution")
        supported_formats = await sync_to_async(self._get_db_feature)(
            connection, "supported_explain_formats"
        )
        all_formats = (None, *supported_formats)
        for format_ in all_formats:
            with self.subTest(format=format_):
                # TODO: Check the captured query when async versions of
                # self.assertNumQueries/CaptureQueriesContext context
                # processors are available.
                result = await SimpleModel.objects.filter(field=1).aexplain(
                    format=format_
                )
                self.assertIsInstance(result, str)
                self.assertTrue(result)
                if not format_:
                    continue
                if format_.lower() == "xml":
                    try:
                        xml.etree.ElementTree.fromstring(result)
                    except xml.etree.ElementTree.ParseError as e:
                        self.fail(f"QuerySet.aexplain() result is not valid XML: {e}")
                elif format_.lower() == "json":
                    try:
                        json.loads(result)
                    except json.JSONDecodeError as e:
                        self.fail(f"QuerySet.aexplain() result is not valid JSON: {e}")

    async def test_raw(self):
        sql = "SELECT id, field FROM async_simplemodel WHERE created=%s"
        qs = SimpleModel.objects.raw(sql, [self.s1.created])
        self.assertEqual([o async for o in qs], [self.s1])


# for all the test methods on AsyncQuerySetTest
# we will add a variant, that first opens a new
# async connection


def _tests():
    return [(attr, getattr(AsyncQuerySetTest, attr)) for attr in dir(AsyncQuerySetTest)]


def wrap_test(original_test, test_name):
    """
    Given an async test, provide an async test that
    is generating a new connection
    """
    new_test_name = test_name + "_new_cxn"

    async def wrapped_test(self):
        async with new_connection(force_rollback=True):
            await original_test(self)

    wrapped_test.__name__ = new_test_name
    return (new_test_name, wrapped_test)


for test_name, test in _tests():
    new_name, new_test = wrap_test(test, test_name)
    setattr(AsyncQuerySetTest, new_name, new_test)
