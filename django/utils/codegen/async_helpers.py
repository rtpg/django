from collections import namedtuple
import libcst as cst
from libcst import FunctionDef, ClassDef, Name, Decorator
from libcst.helpers import get_full_name_for_node

import argparse
from ast import literal_eval
from typing import Union

import libcst as cst
import libcst.matchers as m
from libcst.codemod import CodemodContext, VisitorBasedCodemodCommand
from libcst.codemod.visitors import AddImportsVisitor


DecoratorInfo = namedtuple("DecoratorInfo", ["from_codegen", "unasync", "async_unsafe"])


class UnasyncifyMethod(cst.CSTTransformer):
    """
    Make a non-sync version of the method
    """

    def __init__(self):
        self.await_depth = 0

    def visit_Await(self, node):
        self.await_depth += 1

    def leave_Await(self, original_node, updated_node):
        self.await_depth -= 1
        # we just remove the actual await
        return updated_node.expression

    NAMES_TO_REWRITE = {
        "aconnection": "connection",
        "ASYNC_TRUTH_MARKER": "False",
        "acursor": "cursor",
    }

    def leave_Name(self, original_node, updated_node):
        # some names will get rewritten because we know
        # about them
        if updated_node.value in self.NAMES_TO_REWRITE:
            return updated_node.with_changes(
                value=self.NAMES_TO_REWRITE[updated_node.value]
            )
        return updated_node

    def unasynced_function_name(self, func_name: str) -> str | None:
        """
        Return the function name for an unasync version of this
        function (or None if there is no unasync version)
        """
        if func_name.startswith("a"):
            return func_name[1:]
        elif func_name.startswith("_a"):
            return "_" + func_name[2:]
        else:
            return None

    def leave_Call(self, original_node, updated_node):
        if self.await_depth == 0:
            # we only transform calls that are part of
            # an await expression
            return updated_node

        if isinstance(updated_node.func, cst.Name):
            func_name: cst.Name = updated_node.func
            unasync_name = self.unasynced_function_name(updated_node.func.value)
            if unasync_name is not None:
                # let's transform it by removing the a
                return updated_node.with_changes(
                    func=updated_node.func.with_changes(
                        name=func_name.with_changes(value=unasync_name)
                    )
                )
        elif isinstance(updated_node.func, cst.Attribute):
            func_name: cst.Name = updated_node.func.attr
            unasync_name = self.unasynced_function_name(updated_node.func.attr.value)
            if unasync_name is not None:
                # let's transform it by removing the a
                return updated_node.with_changes(
                    func=updated_node.func.with_changes(
                        attr=func_name.with_changes(value=unasync_name)
                    )
                )
        return updated_node

    def leave_If(self, original_node, updated_node):

        # checking if the original if was "if ASYNC_TRUTH_MARKER"
        # (the updated node would have turned this to if False)
        if (
            isinstance(original_node.test, cst.Name)
            and original_node.test.value == "ASYNC_TRUTH_MARKER"
        ):
            if updated_node.orelse is not None:
                if isinstance(updated_node.orelse, cst.Else):
                    # unindent
                    return cst.FlattenSentinel(updated_node.orelse.body.body)
                else:
                    # we seem to have elif continuations so use that
                    return updated_node.orelse
            else:
                # if there's no else branch we just remove the node
                return cst.RemovalSentinel.REMOVE
        return updated_node

    def leave_CompFor(self, original_node, updated_node):
        if updated_node.asynchronous is not None:
            return updated_node.with_changes(asynchronous=None)
        else:
            return updated_node

    def leave_For(self, original_node, updated_node):
        if updated_node.asynchronous is not None:
            return updated_node.with_changes(asynchronous=None)
        else:
            return updated_node

    def leave_With(self, original_node, updated_node):
        if updated_node.asynchronous is not None:
            return updated_node.with_changes(asynchronous=None)
        else:
            return updated_node


class UnasyncifyMethodCommand(VisitorBasedCodemodCommand):
    DESCRIPTION = "Transform async methods to sync ones"

    def __init__(self, context: CodemodContext) -> None:
        super().__init__(context)
        self.class_stack: list[ClassDef] = []

    def visit_ClassDef(self, original_node):
        self.class_stack.append(original_node)
        return True

    def leave_ClassDef(self, original_node, updated_node):
        self.class_stack.pop()
        return updated_node

    def should_be_unasyncified(self, node: FunctionDef):
        method_name = get_full_name_for_node(node.name)
        # XXX do other checks here as well?
        return (
            node.asynchronous
            and method_name.startswith("a")
            and method_name == "ainit_connection_state"
        )

    def label_as_codegen(self, node: FunctionDef, async_unsafe: bool) -> FunctionDef:

        from_codegen_marker = Decorator(decorator=Name("from_codegen"))
        AddImportsVisitor.add_needed_import(
            self.context, "django.utils.codegen", "from_codegen"
        )

        decorators_to_add = [from_codegen_marker]
        if async_unsafe:
            async_unsafe_marker = Decorator(decorator=Name("async_unsafe"))
            AddImportsVisitor.add_needed_import(
                self.context, "django.utils.asyncio", "async_unsafe"
            )
            decorators_to_add.append(async_unsafe_marker)
        # we remove generate_unasynced_codegen
        return node.with_changes(decorators=[*decorators_to_add, *node.decorators[1:]])

    def codegenned_func(self, node: FunctionDef) -> bool:
        for decorator in node.decorators:
            if (
                isinstance(decorator.decorator, Name)
                and decorator.decorator.value == "from_codegen"
            ):
                return True
        return False

    generate_unasync_pattern = m.Call(
        func=m.Name(value="generate_unasynced"),
    )

    generated_keyword_pattern = m.Arg(
        keyword=m.Name(value="async_unsafe"),
        value=m.Name(value="True"),
    )

    def decorator_info(self, node: FunctionDef) -> DecoratorInfo:
        from_codegen = False
        unasync = False
        async_unsafe = False

        # we only consider the top decorator, and will copy everything else
        if node.decorators:
            decorator = node.decorators[0]
            if isinstance(decorator.decorator, cst.Name):
                if decorator.decorator.value == "from_codegen":
                    from_codegen = True
            elif m.matches(decorator.decorator, self.generate_unasync_pattern):
                unasync = True
                args = decorator.decorator.args
                if len(args) == 0:
                    async_unsafe = False
                elif len(args) == 1:
                    # assert that it's async_unsafe, our only supported
                    # keyword for now
                    assert m.matches(
                        args[0], self.generated_keyword_pattern
                    ), f"We only support async_unsafe=True as a keyword argument, got {args}"
                    async_unsafe = True
                else:
                    raise ValueError(
                        "generate_unasynced only supports 0 or 1 arguments"
                    )
        return DecoratorInfo(from_codegen, unasync, async_unsafe)

    def decorator_names(self, node: FunctionDef) -> list[str]:
        # get the names of the decorators on this function
        # this doesn't try very hard
        return [
            decorator.decorator.value
            for decorator in node.decorators
            if isinstance(decorator.decorator, Name)
        ]

    def leave_FunctionDef(self, original_node: FunctionDef, updated_node: FunctionDef):
        decorator_info = self.decorator_info(updated_node)
        # if we are looking at something that's already codegen, drop it
        # (it will get regenerated)
        if decorator_info.from_codegen:
            return cst.RemovalSentinel.REMOVE

        if decorator_info.unasync:
            method_name = get_full_name_for_node(updated_node.name)
            if method_name[0] != "a" and method_name[:2] != "_a":
                raise ValueError(
                    "Expected an async method with unasync codegen to start with 'a' or '_a'"
                )
            if method_name[0] == "a":
                new_name = method_name[1:]
            else:
                new_name = "_" + method_name[2:]

            unasynced_func = updated_node.with_changes(
                name=Name(new_name),
                asynchronous=None,
            )
            unasynced_func = self.label_as_codegen(
                unasynced_func, async_unsafe=decorator_info.async_unsafe
            )
            unasynced_func = unasynced_func.visit(UnasyncifyMethod())

            # while here the async version is the canonical version, we place
            # the unasync version up on top
            return cst.FlattenSentinel([unasynced_func, updated_node])
        else:
            return updated_node
