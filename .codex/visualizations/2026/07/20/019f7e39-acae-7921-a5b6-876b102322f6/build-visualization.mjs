import fs from 'fs';

const [templatePath, dataPath, outputPath] = process.argv.slice(2);
if (!templatePath || !dataPath || !outputPath) throw new Error('template, data and output paths are required');
const template = fs.readFileSync(templatePath, 'utf8');
const data = fs.readFileSync(dataPath, 'utf8');
const output = template.replace('__BALANCE_DATA__', data);
if (output.includes('__BALANCE_DATA__')) throw new Error('data placeholder was not replaced');
fs.writeFileSync(outputPath, output);
console.log(`${outputPath} ${Buffer.byteLength(output)} bytes`);
