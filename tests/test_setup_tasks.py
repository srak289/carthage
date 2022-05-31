# Copyright (C) 2019, 2020, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os, pytest, os.path, sys, shutil
from carthage.dependency_injection import *
import carthage, carthage.ansible
from carthage.pytest import *
from carthage.setup_tasks import *
from carthage.setup_tasks import SetupTaskContext, TaskWrapperBase
from pathlib import Path

state_dir = Path(__file__).parent.joinpath("test_state")

@pytest.fixture()
def ainjector(ainjector):
    ainjector = ainjector.claim("test_setup.py")
    config = ainjector.injector(carthage.ConfigLayout)
    config.state_dir = state_dir
    os.makedirs(state_dir, exist_ok = True)
    yield ainjector
    shutil.rmtree(state_dir, ignore_errors = True)

class  Stampable(SetupTaskMixin, AsyncInjectable):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = self.__class__.__qualname__
    def __repr__(self):
        return f"<setup_test object for {self.name} test>"

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls.stamp_path = state_dir.joinpath(str(id(cls)))

@async_test
async def test_basic_setup(ainjector):
    called = 0

    class c(Stampable):
        @setup_task("test_stamp")
        def test_stamp_task(self):
            nonlocal called
            called += 1
    assert called == 0
    assert not c.check_stamp( c, "test_stamp_task")[0]
    c_obj = await ainjector(c)
    assert called == 1
    assert c_obj.check_stamp( "test_stamp_task")[0]
    c_obj2 = await ainjector(c)
    assert c_obj is not c_obj2
    assert called == 1

@async_test
async def test_check_completed(ainjector):
    called = 0
    is_completed = True
    class c(Stampable):
        @setup_task("check_completed")
        def setup_check_completed(self):
            nonlocal called
            called +=1
        @setup_check_completed.check_completed()
        def setup_check_completed(self):
            return is_completed
    c_1 = await ainjector(c)
    assert called == 0
    is_completed = False
    c_2 = await ainjector(c)
    assert called == 1
    assert not c_1.check_stamp("check_completed")[0]

@async_test
async def test_invalidator(ainjector):
    called = 0
    class c(Stampable):
        @setup_task("test_invalidator")
        def setup_invalidator(self):
            nonlocal called
            called += 1
        @setup_invalidator.invalidator()
        def setup_invalidator(self, **kwargs):
            return False
    assert not c.check_stamp(c, "setup_invalidator")[0]
    await ainjector(c)
    assert called == 1
    assert c.check_stamp( c, "setup_invalidator")[0]
    await ainjector(c)
    assert called == 2


@async_test
async def test_failure_forces_rerun(ainjector):
    "If a task is explicitly run and fails, does the stamp get reset?"
    called = 0
    should_fail = False
    class c(Stampable):
        @setup_task("test_error_explicit")
        def setup_test_error_explicit(self):
            nonlocal called
            called += 1
            if should_fail:
                raise RuntimeError

    assert not c.check_stamp( c, "setup_test_error_explicit")[0]
    await ainjector(c)
    assert called == 1
    should_fail = True
    o = await ainjector(c)
    assert called == 1
    with pytest.raises(RuntimeError):
        o.setup_test_error_explicit()
    assert called == 2
    should_fail = False
    await ainjector(c)
    assert called == 3

@async_test
async def test_order_override(ainjector):
    two_called = False
    class c (Stampable):
        @setup_task("one")
        def one(self): pass

        @setup_task("foo", order = 9200)
        def two(self):
            nonlocal two_called
            two_called = True

        @setup_task("three")
        def three(self):
            assert two_called is True

        @setup_task("before_two", before = two)
        def before_two(self):
            assert two_called is False

    assert c.one.order < 9200
    assert c.before_two.order < c.two.order
    assert c.three.order > c.two.order
    await ainjector(c)


@async_test
async def test_mako_task(ainjector):
    class bar(Stampable):

        templates = mako_task("test.mako")

        template_2 = inject(name = InjectionKey("name")) \
            (mako_task("template-2.mako"))

    ainjector.add_provider(InjectionKey("name"), "the name")
    res = await ainjector(bar)
    template_2 = res.stamp_path.joinpath("template-2").read_text()
    template_2_expected = state_dir.parent.joinpath("template-2.expected").read_text()
    assert template_2 == template_2_expected


@async_test
async def test_hash_func(ainjector):
    fake_hash = "30"
    test_hash_run = 0
    class c(Stampable):

        @setup_task("Test hash")
        def test_hash(self):
            nonlocal test_hash_run
            test_hash_run +=1

        @test_hash.hash()
        def test_hash(self):
            return fake_hash
    o = await ainjector(c)
    test_hash_run = 0
    o.test_hash()
    assert test_hash_run == 1
    await o.run_setup_tasks()
    assert test_hash_run == 1
    fake_hash = "45"
    await o.run_setup_tasks()
    assert test_hash_run == 2
@async_test
async def test_mako_hash(ainjector):
    class c(Stampable):

        mt = mako_task("test_hash.mako",
                       fake_hash = InjectionKey("fake_hash"),
                       real_value = InjectionKey("real_value"))

    def get_fake_hash(): return fake_hash
    def get_real_value(): return real_value
    ainjector.add_provider(InjectionKey("fake_hash"), get_fake_hash)
    ainjector.add_provider(InjectionKey("real_value"), get_real_value)
    real_value = "foo"
    fake_hash = "30"
    o = await ainjector(c)
    def output():
        return Path(o.stamp_path).joinpath("test_hash").read_text()
    assert output() == "foo"
    real_value = "bar"
    await o.run_setup_tasks()
    assert output() == "foo"
    fake_hash = "90"
    await o.run_setup_tasks()
    assert output() == "bar"
@async_test
async def test_setup_task_context(ainjector):
    class  ContextTest(Stampable):

        @setup_task("Test context")
        async def test_context(self):
            nonlocal called
            ctx = current_instantiation()
            assert isinstance(ctx, SetupTaskContext)
            assert ctx.instance is self
            assert ctx.parent.key is InjectionKey(ContextTest)
            called = 1

    ainjector.add_provider(ContextTest)
    called = 0
    await ainjector.get_instance_async(ContextTest)
    assert called == 1
    

@async_test
async def test_add_setup_task(ainjector):
    def task_a(self):
        nonlocal task_a_called
        task_a_called = 1
    @setup_task("TaskWrapper test")
    def task_b(self):
        nonlocal task_b_called
        task_b_called = 1
    task_a_called = False
    task_b_called = False
    class C(Stampable): pass
    o = await ainjector(C)

    assert not task_a_called
    assert not task_b_called
    o.add_setup_task(task_a, description="call task_a")
    assert isinstance(task_b, TaskWrapperBase)
    o.add_setup_task(task_b)
    await o.run_setup_tasks()
    assert task_a_called
    assert task_b_called
    
