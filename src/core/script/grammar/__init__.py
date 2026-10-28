"""AIOS Script grammar (v1) package — lexer (DSL-2), parser (DSL-3), AST (DSL-1)."""

from __future__ import annotations

from src.core.script.grammar.ast import (
    GRAMMAR_VERSION,
    BinaryExpr,
    CallExpr,
    Decl,
    Expr,
    Identifier,
    InputDecl,
    LetDecl,
    NotExpr,
    NumberLiteral,
    OrderDecl,
    PlotDecl,
    PostfixExpr,
    Program,
    ScriptNode,
    SignalDecl,
    TypeNode,
    UnaryExpr,
    program_from_dict,
    to_dict,
)

__all__ = [
    "GRAMMAR_VERSION",
    "BinaryExpr",
    "CallExpr",
    "Decl",
    "Expr",
    "Identifier",
    "InputDecl",
    "LetDecl",
    "NotExpr",
    "NumberLiteral",
    "OrderDecl",
    "PlotDecl",
    "PostfixExpr",
    "Program",
    "ScriptNode",
    "SignalDecl",
    "TypeNode",
    "UnaryExpr",
    "program_from_dict",
    "to_dict",
]
