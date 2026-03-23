import java.io.File;
import java.io.FileWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class OpenCrowDecompileFunction extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        String functionName = args.length > 0 ? args[0] : "";
        String functionAddress = args.length > 1 ? args[1] : "";
        String outputPath = args.length > 2 ? args[2] : "";

        if (outputPath.isEmpty()) {
            throw new Exception("Missing output path argument.");
        }

        Function target = null;
        if (!functionAddress.isEmpty()) {
            target = currentProgram.getFunctionManager().getFunctionAt(toAddr(functionAddress));
        }
        if (target == null && !functionName.isEmpty()) {
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                Function candidate = functions.next();
                if (candidate.getName().equals(functionName)) {
                    target = candidate;
                    break;
                }
            }
        }
        if (target == null) {
            throw new Exception("Function not found.");
        }

        DecompInterface iface = new DecompInterface();
        iface.openProgram(currentProgram);
        var result = iface.decompileFunction(target, 120, monitor);
        if (!result.decompileCompleted()) {
            throw new Exception("Decompilation failed: " + result.getErrorMessage());
        }

        String text = result.getDecompiledFunction().getC();
        try (FileWriter writer = new FileWriter(new File(outputPath))) {
            writer.write(text);
        }

        println(target.getName());
        println(target.getEntryPoint().toString());
        println(outputPath);
    }
}
