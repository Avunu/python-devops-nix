// A workspace member: its own dependency, resolved from its own node_modules.
module.exports = { answer: require("is-number")(42) };
