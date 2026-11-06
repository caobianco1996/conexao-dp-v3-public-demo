(() => {
  const form = document.querySelector("[data-tax-simulator]");
  if (!form) return;
  const mode = form.elements.namedItem("mode");
  const inss = form.elements.namedItem("calculateInss");
  const irrf = form.elements.namedItem("calculateIrrf");
  const competence = form.elements.namedItem("competence");
  const paymentDate = form.elements.namedItem("paymentDate");

  const synchronize = () => {
    const annual = mode.value === "THIRTEENTH";
    if (annual) irrf.value = "0";
    irrf.disabled = annual;
    competence.required = inss.value === "1";
    paymentDate.required = irrf.value === "1" && !annual;
    const source = form.elements.namedItem("inssSource");
    const manual = form.elements.namedItem("manualInss");
    const manualSource = form.elements.namedItem("manualInssSource");
    const hasOtherBases = Array.from(form.querySelectorAll('[name^="other_"][name$="_base"]')).some(input => input.value.trim() !== "");
    const needsManual = irrf.value === "1" && (inss.value !== "1" || hasOtherBases);
    if (source) source.value = needsManual ? "MANUAL" : (source.value || "CALCULATED");
    if (manual) manual.required = needsManual;
    if (manualSource) manualSource.required = needsManual;
  };

  mode.addEventListener("change", synchronize);
  inss.addEventListener("change", synchronize);
  irrf.addEventListener("change", synchronize);
  form.addEventListener("input", synchronize);
  synchronize();
})();
