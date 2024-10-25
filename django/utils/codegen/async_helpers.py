import libcst as cst
from libcst import FunctionDef, ClassDef, Name
from libcst.helpers import get_full_name_for_node

import argparse
from ast import literal_eval
from typing import Union

import libcst as cst
from libcst.codemod import CodemodContext, VisitorBasedCodemodCommand
from libcst.codemod.visitors import AddImportsVisitor


class UnasyncifyMethodCommand(VisitorBasedCodemodCommand):
    DESCRIPTION = "Transform async methods to sync ones"

    def __init__(self):
        self.class_stack: list[ClassDef] = []

    def leave_FunctionDef(self, original_node: FunctionDef, updated_node: FunctionDef):
        method_name = get_full_name_for_node(original_node.name)

        # Check if the method name starts with 'a'
        if method_name.startswith("a"):
            print(method_name)
            raise ValueError()
            new_method_name = method_name[1:]  # Remove the leading 'a'

            # Create a duplicate function with the new name
            new_function = updated_node.with_changes(name=Name(value=new_method_name))

            # Return the original and the new duplicate function
            return cst.FlattenSentinel([updated_node, new_function])

        # If the method doesn't start with 'a', return it unchanged
        return updated_node
