// Renders the SCDoc help of one package to HTML (docs_plan.md, build step 3). Runs in
// the sandbox after `magnet install` (step 2), with the installed packages as the only
// extensions (no language config needed, its absolute paths would pin the mounts):
//
//   sclang -a --include-path /usr/local/share/SuperCollider/SCClassLibrary \
//       --include-path PACKAGES /opt/magnet/render.scd PACKAGES NAME OUT
//
// PACKAGES is the project's magnet_packages (e.g. mounted read-only at /packages), its
// folders are the package (NAME, normalised) and its dependencies; OUT gets the result.
//
// SCDoc indexes every document (SuperCollider's, the package's and its dependencies'),
// copies the other files of every HelpSource folder (static/, images, …) to its target
// and renders the package's own .schelp files only (no pages for undocumented classes,
// .ext.schelp additions to other documents are left out). It renders into /tmp, copied
// to OUT at the end (SCDoc's File.copy fails on some bind mounts, e.g. podman machine's
// on macOS). `docmap.json` records who owns each document, for rewriting the links
// between them later (docs/rewrite.py):
//
//   {"sclang_version": "3.14.1",
//    "documents": {"Classes/Foo": {"owner": "package", "title": "Foo", "summary": "…",
//                                  "categories": ["…"]},
//                  "Classes/FooHelper": {"owner": "package", "undocumented": true},
//                  "Classes/Bar": {"owner": "dependency", "dependency": "bar"},
//                  "Classes/SinOsc": {"owner": "sc"}, …},
//    "files": ["images/foo.png", …],
//    "errors": [{"document": "Classes/Foo", "error": "…"}]}
//
// The package's documents with a title are the rendered ones; "undocumented" marks classes
// without help (no page, wherever they belong), "files" are the package's non-schelp help
// files (relative to the output), the owner "other" is anything else (e.g. classes magnet
// generates). Exits with 0 once docmap.json is written (documents that failed are listed
// in "errors"), else with 1.
(
var packagesDir, name, packageDir, out, target = "/tmp/scdoc";
var ownerOf, json, quote, writeDocMap;
var documents = Dictionary(), files = [], errors = [];

// "package", "dependency" (with its folder name), "sc" or "other" for a file
ownerOf = {|file|
	var dependency;
	case
	{ file.beginsWith(packageDir ++ "/") } { ["package"] }
	{ file.beginsWith(packagesDir ++ "/") } {
		dependency = file.drop(packagesDir.size + 1).split($/);
		// files right in magnet_packages, e.g. the generated server options class
		if(dependency.size > 1) { ["dependency", dependency.first] } { ["other"] }
	}
	{
		file.beginsWith(Platform.classLibraryDir ++ "/")
		or: { file.beginsWith(SCDoc.helpSourceDir ++ "/") }
	} { ["sc"] }
	{ ["other"] }
};

quote = {|string|
	String.streamContents {|stream|
		stream << $";
		string.do {|char|
			case
			{ char == $" } { stream << "\\\"" }
			{ char == $\\ } { stream << "\\\\" }
			// control characters; bytes of UTF-8 sequences are negative and pass as is
			{ char.ascii >= 0 and: { char.ascii < 32 } } {
				stream << "\\u" << char.ascii.asHexString(4)
			}
			{ stream << char }
		};
		stream << $";
	}
};

json = {|value|
	case
	{ value.isNil } { "null" }
	{ value === true } { "true" }
	{ value === false } { "false" }
	{ value.isString or: { value.isKindOf(Symbol) } } { quote.(value.asString) }
	{ value.isNumber } { value.asString }
	{ value.isKindOf(Dictionary) } {
		"{" ++ value.keys.asArray.sort.collect {|key|
			quote.(key.asString) ++ ": " ++ json.(value[key])
		}.join(", ") ++ "}"
	}
	{ value.isSequenceableCollection } { "[" ++ value.collect(json).join(", ") ++ "]" }
	// e.g. a class among the categories a package declares
	{ quote.(value.asString) }
};

writeDocMap = {
	var path = target +/+ "docmap.json";
	var map = Dictionary[
		"sclang_version" -> Main.version,
		"documents" -> documents,
		"files" -> files.sort,
		"errors" -> errors,
	];
	File.use(path, "w") {|file| file.write(json.(map)) };
};

try {
	if(thisProcess.argv.size != 3) { Error("expected the arguments PACKAGES NAME OUT").throw };
	#packagesDir, name, out = thisProcess.argv.collect {|path|
		path.standardizePath.withoutTrailingSlash
	};
	packageDir = packagesDir +/+ name;
	if(File.exists(packageDir).not) { Error("% doesn't exist".format(packageDir)).throw };

	SCDoc.helpTargetDir = target;
	// index from scratch, which also copies the static files into the target
	SCDoc.indexAllDocuments(clearCache: true);

	SCDoc.documents.keysValuesDo {|path, doc|
		var undocumented = doc.isUndocumentedClass == true;
		var file = case
		{ doc.oldHelp.notNil } { nil }
		{ undocumented } { doc.klass.filenameSymbol.asString }
		{ doc.fullPath };
		var owner = if(file.isNil) { ["other"] } { ownerOf.(file) };
		var entry = Dictionary["owner" -> owner.first];

		if(owner.first == "dependency") { entry["dependency"] = owner[1] };
		if(undocumented) { entry["undocumented"] = true };
		if(owner.first == "package" and: { undocumented.not }) {
			entry["title"] = doc.title;
			entry["summary"] = doc.summary;
			entry["categories"] = doc.categories.asArray;
		};
		documents[path] = entry;
	};

	documents.keys.asArray.sort.do {|path|
		var doc = SCDoc.documents[path];
		if(documents[path]["title"].notNil) {
			"magnet: rendering %".format(path).postln;
			try {
				SCDoc.parseAndRender(doc);
				// parse errors are only posted, without the document being written
				if(File.exists(doc.destPath).not) { Error("not rendered, see the log").throw };
			} {|error|
				errors = errors.add(Dictionary[
					"document" -> path,
					"error" -> error.errorString
				]);
				error.reportError;
			};
		};
	};

	SCDoc.helpSourceDirs.select {|dir| ownerOf.(dir ++ "/").first == "package" }.do {|dir|
		PathName(dir).filesDo {|file|
			if(file.extension != "schelp") {
				files = files.add(file.fullPath.drop(dir.size + 1));
			};
		};
	};

	writeDocMap.();
	if("cp -R % %".format((target ++ "/.").shellQuote, out.shellQuote).systemCmd != 0) {
		Error("copying the docs to % failed".format(out)).throw
	};
	"magnet: rendered % documents to %".format(
		documents.count {|doc| doc["title"].notNil } - errors.size, out
	).postln;
	0.exit;
} {|error|
	error.reportError;
	"magnet: rendering the docs failed".postln;
	1.exit;
};
)
