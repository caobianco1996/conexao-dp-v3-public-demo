"use client";

import { useEffect, useId, useReducer, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import styles from "./quick-calculator.module.css";

const initial = { entry: "0", left: null, operator: null, replace: true, lastAction: "result", lastOperator: null, lastOperand: null, history: "" };
const symbols = { "+": "+", "-": "−", "*": "×", "/": "÷" };
const resultText = value => Number.isFinite(value) ? String(Number(value.toPrecision(12))) : "Erro";
const calculate = (left, operator, right) => operator === "+" ? left + right : operator === "-" ? left - right : operator === "*" ? left * right : right === 0 ? NaN : left / right;
const displayText = entry => {
  if (entry === "Erro") return entry;
  if (/e/i.test(entry)) return entry.replace(".", ",");
  const [integer, decimals] = entry.split(".");
  return Number(integer).toLocaleString("pt-BR", { maximumFractionDigits: 0 }) + (decimals !== undefined ? `,${decimals}` : "");
};

function reducer(state, key) {
  if (key === "clear") return { ...initial };
  if (/^\d$/.test(key)) {
    const current = state.entry === "Erro" ? initial : state;
    if (!current.replace && current.entry.replace(/\D/g, "").length >= 14) return current;
    const entry = current.replace || current.entry === "0" ? key : current.entry === "-0" ? `-${key}` : current.entry + key;
    return { ...current, entry, replace: false, lastAction: "value", lastOperator: null, lastOperand: null, history: "" };
  }
  if (key === "decimal") {
    const current = state.entry === "Erro" ? initial : state;
    const entry = current.replace ? "0." : current.entry.includes(".") || /e/i.test(current.entry) ? current.entry : `${current.entry}.`;
    return { ...current, entry, replace: false, lastAction: "value", lastOperator: null, lastOperand: null, history: "" };
  }
  if (state.entry === "Erro") return state;
  if (key === "backspace") {
    if (state.replace) return state;
    const shortened = state.entry.slice(0, -1);
    return { ...state, entry: shortened && shortened !== "-" ? shortened : "0", lastAction: "value" };
  }
  if (key === "sign") return { ...state, entry: state.entry.startsWith("-") ? state.entry.slice(1) : `-${state.entry}`, lastAction: "value" };
  if (key === "percent") {
    const value = Number(state.entry);
    const percent = state.left !== null && ["+", "-"].includes(state.operator) ? state.left * value / 100 : value / 100;
    return { ...state, entry: resultText(percent), replace: true, lastAction: "value", lastOperator: null, lastOperand: null, history: "" };
  }
  if (Object.hasOwn(symbols, key)) {
    let left = Number(state.entry);
    if (state.operator && state.left !== null) {
      left = state.lastAction === "operator" ? state.left : calculate(state.left, state.operator, Number(state.entry));
    }
    const entry = resultText(left);
    if (entry === "Erro") return { ...initial, entry };
    return { ...state, entry, left, operator: key, replace: true, lastAction: "operator", lastOperator: null, lastOperand: null, history: "" };
  }
  if (key === "equals") {
    const operator = state.operator || state.lastOperator;
    const left = state.operator ? state.left : Number(state.entry);
    const right = state.operator ? Number(state.entry) : state.lastOperand;
    if (!operator || left === null || right === null) return state;
    const entry = resultText(calculate(left, operator, right));
    if (entry === "Erro") return { ...initial, entry };
    return { ...initial, entry, lastOperator: operator, lastOperand: right, history: `${displayText(String(left))} ${symbols[operator]} ${displayText(String(right))} =` };
  }
  return state;
}

const keys = [
  ["clear", "C", "Limpar cálculo"], ["backspace", "⌫", "Apagar último dígito"], ["percent", "%", "Porcentagem"], ["/", "÷", "Dividir"],
  ["7", "7"], ["8", "8"], ["9", "9"], ["*", "×", "Multiplicar"],
  ["4", "4"], ["5", "5"], ["6", "6"], ["-", "−", "Subtrair"],
  ["1", "1"], ["2", "2"], ["3", "3"], ["+", "+", "Somar"],
  ["sign", "±", "Inverter sinal"], ["0", "0"], ["decimal", ",", "Separador decimal"], ["equals", "=", "Calcular resultado"],
];

export default function QuickCalculator({ children }) {
  const [open, setOpen] = useState(false);
  const [state, press] = useReducer(reducer, initial);
  const wrapRef = useRef(null), panelRef = useRef(null), toggleRef = useRef(null);
  const id = useId();
  const pathname = usePathname();
  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) toggleRef.current?.focus();
  };

  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const outside = event => { if (!wrapRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("click", outside);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("click", outside);
    };
  }, [open]);

  const handleKey = event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); return; }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest("[data-calculator-close]")) return;
    const key = /^\d$/.test(event.key) ? event.key : ({ ".": "decimal", ",": "decimal", "/": "/", "*": "*", "-": "-", "+": "+", "Enter": "equals", "=": "equals", "Backspace": "backspace", "Delete": "clear", "c": "clear", "C": "clear", "%": "percent" })[event.key];
    if (key) { event.preventDefault(); press(key); }
  };
  const display = displayText(state.entry);
  const expression = state.operator && state.left !== null ? `${displayText(String(state.left))} ${symbols[state.operator]}` : state.history;

  return <div className={`quick-calc-wrap ${styles.wrap}`} ref={wrapRef}>
    <button className="btn btn-outline-secondary btn-sm header-icon-action" type="button" ref={toggleRef} onClick={() => setOpen(value => !value)} aria-label={open ? "Fechar calculadora" : "Abrir calculadora"} title="Calculadora rápida" aria-haspopup="dialog" aria-expanded={open} aria-controls={id}>{children}</button>
    {open && <section className={`quick-calc ${styles.panel}`} id={id} ref={panelRef} role="dialog" aria-modal="false" aria-labelledby={`${id}-title`} aria-describedby={`${id}-help`} tabIndex={-1} onKeyDown={handleKey}>
      <header><h2 id={`${id}-title`}>Calculadora</h2><button className="quick-calc-close" type="button" aria-label="Fechar calculadora" data-calculator-close onClick={() => close(true)}><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>
      <div className={styles.screen}><span className={styles.expression}>{expression || "\u00a0"}</span><output className={`quick-calc-display ${styles.display}`} data-long={display.length > 14} aria-live="polite" aria-atomic="true" aria-label="Resultado" title={display}>{display}</output></div>
      <div className="quick-calc-keys" role="group" aria-label="Teclas da calculadora">{keys.map(([key, label, accessible]) => <button key={key} type="button" className={key === "equals" ? "equals" : symbols[key] ? "operator" : ""} aria-label={accessible || label} onClick={() => press(key)}>{label}</button>)}</div>
      <small id={`${id}-help`}>Use o teclado e Enter para calcular. Esc fecha.</small>
    </section>}
  </div>;
}
