import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(path.join(process.env.V3_ARTIFACT_MODULES, "..", "package.json"));
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(require.resolve("@oai/artifact-tool")).href);
let input = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) input += chunk;
const data = JSON.parse(input);
const report = data.reports.selected;
if (report.pending || report.rows.length !== report.total) throw new Error("O relatório não está completo para exportação.");
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Relatório");
const criteria = workbook.worksheets.add("Critérios");
sheet.showGridLines = false;
criteria.showGridLines = false;
sheet.tabColor = "#0A5C68";
criteria.tabColor = "#77929C";
const literal = value => typeof value === "string" && value.startsWith("=") ? `'${value}` : value;
const col = index => { let value = index + 1, name = ""; while (value) { name = String.fromCharCode(65 + (value - 1) % 26) + name; value = Math.floor((value - 1) / 26); } return name; };
const generated = new Date(data.meta.generatedAt).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
const lastCol = col(report.columns.length - 1);
const lastRow = 1 + report.rows.length;
const companyLabels=new Map(data.options.companies.map(item=>[item.value,item.label]));
const base = sheet.getRange(`A1:${lastCol}${Math.max(1,lastRow)}`);
base.format.font = { name: "Arial", size: 10, color: "#18374E" };
base.format.verticalAlignment = "center";
base.format.rowHeight = 23;
sheet.getRange(`A1:${lastCol}1`).values = [report.columns];
if (report.rows.length) {
  const rows = report.rows.map(row => row.map((value,index) => {
    const heading = report.columns[index];
    const kind = report.columnTypes?.[index];
    if (kind === "percent" && typeof value === "number") return value / 100;
    if (heading === "Mês" && /^\d{2}\/\d{4}$/.test(String(value))) { const [m,y]=value.split("/").map(Number); return new Date(Date.UTC(y,m-1,1)); }
    if (typeof value === "string" && (kind === "date" || kind === "datetime" || /admissão|desligamento|vencimento|revisão/i.test(heading)) && /^\d{2}\/\d{2}\/\d{4}( \d{2}:\d{2})?$/.test(value)) {
      const [datePart,timePart="00:00"] = value.split(" "); const [d,m,y] = datePart.split("/").map(Number); const [h,min] = timePart.split(":").map(Number);
      return new Date(Date.UTC(y,m-1,d,h,min));
    }
    return literal(value);
  }));
  sheet.getRange(`A2:${lastCol}${lastRow}`).values = rows;
  const table = sheet.tables.add(`A1:${lastCol}${lastRow}`, true, "RelatorioDados");
  table.showFilterButton = true;
  table.showBandedColumns = false;
}
for (let index=0; index<report.columns.length; index++) {
  const heading=report.columns[index], letter=col(index), kind=report.columnTypes?.[index];
  const range=sheet.getRange(`${letter}1:${letter}${Math.max(1,lastRow)}`);
  range.format.columnWidth = /código|matrícula|admissão|desligamento|vencimento|revisão/i.test(heading) ? 16 : /nome|empresa|alteração|responsável|justificativa|faixa|salário|sexo|tempo|departamento|cargo|contrato|verificação/i.test(heading) ? 38 : 20;
  if (lastRow < 2) continue;
  const values=sheet.getRange(`${letter}2:${letter}${lastRow}`);
  if(kind === "text") values.setNumberFormat("@");
  else if(kind === "currency") values.setNumberFormat('"R$" #,##0.00');
  else if(kind === "percent") values.setNumberFormat("0.0%");
  else if(kind === "date" || kind === "datetime") values.setNumberFormat(kind === "datetime" ? "dd/mm/yyyy hh:mm" : "dd/mm/yyyy");
  else if (/matrícula|código|origem|nome|empresa|situação|contrato/i.test(heading)) values.setNumberFormat("@");
  else if (/admissão|desligamento|vencimento|revisão/i.test(heading) && report.rows.some(row => /^\d{2}\/\d{2}\/\d{4}/.test(String(row[index])))) values.setNumberFormat(/revisão/i.test(heading) ? "dd/mm/yyyy hh:mm" : "dd/mm/yyyy");
  else if (heading === "Mês") values.setNumberFormat("mm/yyyy");
  else values.setNumberFormat("#,##0.##");
  if (report.rows.some(row=>typeof row[index]==="number")) values.format.horizontalAlignment="right";
}
if(lastRow>=2) { sheet.getRange(`A2:${lastCol}${lastRow}`).format.wrapText=true; sheet.getRange(`A2:${lastCol}${lastRow}`).format.autofitRows(); }
const headers=sheet.getRange(`A1:${lastCol}1`);
headers.format.fill="#0A5C68";
headers.format.font={name:"Arial",size:10,bold:true,color:"#FFFFFF"};
headers.format.horizontalAlignment="center";
headers.format.wrapText=true;
headers.format.rowHeight=36;
headers.format.borders={insideVertical:{style:"thin",color:"#FFFFFF"}};
sheet.freezePanes.freezeRows(1);
const periodFields = report.activeSnapshotDate ? [
  ...(report.movementPeriodIncluded ? [
    ["Início do período de movimentações",new Date(`${data.filters.start}T00:00:00Z`)],
    ["Fim do período de movimentações",new Date(`${data.filters.end}T00:00:00Z`)],
  ] : []),
  ["Situação atual dos ativos em",new Date(`${report.activeSnapshotDate}T00:00:00Z`)],
] : [
  ["Início do período",new Date(`${data.filters.start}T00:00:00Z`)],
  ["Data de corte",new Date(`${data.filters.end}T00:00:00Z`)],
];
const fields=[
  ["Relatório",report.title], ...periodFields, ["Vínculos na seleção",data.meta.filteredLinks],
  ["Empresas incluídas",data.filters.companies.map(key=>companyLabels.get(key)||key).join("; ")||"Todas"],
  ["Empresas excluídas",data.filters.excludeCompanies.map(key=>companyLabels.get(key)||key).join("; ")||"Nenhuma"],
  ["Origem",data.filters.sources.join(", ")||"Todas"], [report.activeSnapshotDate ? "Filtro de situação" : "Situação no corte",({all:"Todas",active:"Ativo",terminated:"Desligado",future:"Admissão futura",unknown:"A conferir"})[data.filters.status]],
  ["População",report.hasPopulation ? report.populationLabel : "Conforme os critérios deste relatório"],
  ["Registros exportados",report.total], ["Gerado em",generated],
  ["Fonte","Cadastros locais da V3, recebidos do SIB e demais origens selecionadas."],
  ["Cobertura",data.meta.scanRunning ? "Importação SIB em andamento. A base é parcial." : "Somente os registros disponíveis na base local."],
];
fields.push(["Colunas incluídas",report.columns.join("; ")]);
for (const [key,label] of Object.entries({contracts:"Contratos",departments:"Departamentos",jobs:"Cargos",states:"UF",branches:"Empresa e filial",reasons:"Motivo ou código de saída"})) if(data.filters[key]?.length) fields.push([label,data.filters[key].join("; ")]);
if(data.filters.q) fields.push(["Busca aplicada",data.filters.q]);
for(const note of report.notes) fields.push(["Critério",note]);
fields.push(["Leitura","O relatório não altera cadastros no Bitti/SIB. A planilha é uma fotografia da seleção na data de geração."]);
criteria.getRange(`A1:B${fields.length+3}`).format.font={name:"Arial",size:10,color:"#18374E"};
criteria.getRange("A2").values=[["Filtros e critérios do relatório"]];
criteria.getRange("A2:B2").format.font={name:"Arial",size:14,bold:true,color:"#0A5C68"};
criteria.getRange(`A4:B${fields.length+3}`).values=fields.map(row=>row.map(literal));
criteria.getRange(`A4:A${fields.length+3}`).format.font={name:"Arial",size:10,bold:true,color:"#35596C"};
criteria.getRange(`A4:A${fields.length+3}`).format.columnWidth=27;
criteria.getRange(`B4:B${fields.length+3}`).format.columnWidth=95;
criteria.getRange(`A4:B${fields.length+3}`).format.wrapText=true;
criteria.getRange(`A4:B${fields.length+3}`).format.verticalAlignment="center";
criteria.getRange(`A4:B${fields.length+3}`).format.autofitRows();
const dateFieldRange = report.activeSnapshotDate
  ? (report.movementPeriodIncluded ? "B5:B7" : "B5:B5")
  : "B5:B6";
criteria.getRange(dateFieldRange).setNumberFormat("dd/mm/yyyy");
if(lastRow>=2) {
  const metric = index => {
    const position = report.columnKeys?.indexOf(`metric_${index}`) ?? index;
    return position >= 0 ? col(position) : null;
  };
  const formula = (target, dependencies, build) => {
    const targetColumn=metric(target), inputs=dependencies.map(metric);
    if(targetColumn && inputs.every(Boolean)) sheet.getRange(`${targetColumn}2:${targetColumn}${lastRow}`).formulas=report.rows.map((_,i)=>[build(i+2,inputs)]);
  };
  if(report.id==="turnover") {
    formula(4,[2,3],(r,[admitted,terminated])=>`=${admitted}${r}-${terminated}${r}`);
    formula(6,[1,5],(r,[opening,closing])=>`=(${opening}${r}+${closing}${r})/2`);
    formula(7,[1,2,3,5],(r,[opening,admitted,terminated,closing])=>`=IF((${opening}${r}+${closing}${r})=0,"",(${admitted}${r}+${terminated}${r})/(${opening}${r}+${closing}${r}))`);
  } else if(report.id==="companies") formula(6,[4,5],(r,[admitted,terminated])=>`=${admitted}${r}-${terminated}${r}`);
  else if(report.columns.includes("Participação (%)")) {
    const count=metric(1);
    if(count) {
      const denominator=report.id==="quality" ? "'Critérios'!$B$7" : `SUM($${count}$2:$${count}$${lastRow})`;
      formula(2,[1],(r,[value])=>`=IF(${denominator}=0,"",${value}${r}/${denominator})`);
    }
  }
}
workbook.recalculate();
const previewIndex=process.argv.indexOf("--preview");
if(previewIndex>=0) {
  const folder=process.argv[previewIndex+1]; await fs.mkdir(folder,{recursive:true});
  for(const [name,range,file] of [["Relatório",`A1:${lastCol}${Math.min(lastRow,14)}`,"relatorio.png"],["Critérios","A1:B12","criterios.png"]]) {
    const image=await workbook.render({sheetName:name,range,scale:1.3,format:"png"});
    await fs.writeFile(path.join(folder,file),new Uint8Array(await image.arrayBuffer()));
  }
  const scan=await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!",options:{useRegex:true,maxResults:10},maxChars:1200});
  await fs.writeFile(path.join(folder,"verificacao.json"),scan.ndjson);
}
const output=await SpreadsheetFile.exportXlsx(workbook);
await output.save(process.argv[2]);
